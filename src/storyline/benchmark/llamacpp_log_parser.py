#!/usr/bin/env python3
"""
llamacpp_log_parser.py

Fetch and parse journalctl logs from a llama.cpp-based server (llamacpp.service)
into per-request performance records.

Supports TWO log formats, auto-detected:

  "legacy"  - the llama-cpp-python server (python -m llama_cpp.server), where
              journalctl wraps each line with "MMM DD HH:MM:SS host proc[pid]:"
              and requests are bounded by "Llama.generate: ..." trigger lines
              and a trailing "INFO: ... HTTP/1.1" 200 OK" line.

  "native"  - the compiled llama-server binary's own logger, where each line
              looks like "0.00.170.225 I srv  llama_server: message" or
              "1.01.276.390 I slot print_timing: id 3 | task 0 | ...". Requests
              are tracked per (slot id, task id), bounded by "launch_slot_" and
              "release" lines.

USAGE EXAMPLES
---------------
# Fetch the last 200 lines from the user unit and print a summary table
python3 llamacpp_log_parser.py --lines 200

# Fetch logs from the last hour and dump JSON
python3 llamacpp_log_parser.py --since "1 hour ago" --output-json out.json

# Fetch logs between two timestamps and dump CSV
python3 llamacpp_log_parser.py --since "2026-07-04 21:00:00" \
    --until "2026-07-04 22:10:00" --output-csv out.csv

# Parse a previously-saved log file instead of calling journalctl
python3 llamacpp_log_parser.py --input-file mylog.txt --output-json out.json

# Force a specific format instead of auto-detecting
python3 llamacpp_log_parser.py --input-file mylog.txt --format native

# System-level unit instead of --user
python3 llamacpp_log_parser.py --system --unit llamacpp --lines 500
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class Checkpoint:
    """A legacy-format HybridCheckpointCache save event."""
    pos: int
    size_mib: float
    total: int
    used_mib: float


@dataclass
class RequestRecord:
    # bookkeeping
    log_format: Optional[str] = None  # "legacy" or "native"
    pid: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    # model info (best-effort)
    model_hint: Optional[str] = None

    # --- native-format slot/task tracking ---
    slot_id: Optional[int] = None
    task_id: Optional[int] = None
    cache_selected_by: Optional[str] = None       # "LRU" or "LCP_similarity"
    cache_similarity: Optional[float] = None       # sim_best, LCP mode only
    cache_f_keep: Optional[float] = None           # LCP mode only
    cache_update_ms: Optional[float] = None        # "prompt cache update took X ms"
    forced_full_reprocess: bool = False            # cache miss forced full re-eval
    erased_checkpoint: bool = False
    context_checkpoints: List[Dict[str, Any]] = field(default_factory=list)
    n_tokens_final: Optional[int] = None           # from "release" line
    truncated: Optional[bool] = None

    # --- legacy-format cache / prefix behavior ---
    context_reset: bool = False
    rollback_triggered: bool = False
    prefix_match_hit: Optional[int] = None
    prompt_tokens_remaining: Optional[int] = None
    checkpoints: List[Checkpoint] = field(default_factory=list)

    # --- shared perf fields (llama_perf_context_print / print_timing) ---
    load_time_ms: Optional[float] = None

    prompt_eval_time_ms: Optional[float] = None
    prompt_eval_tokens: Optional[int] = None
    prompt_eval_ms_per_token: Optional[float] = None
    prompt_eval_tokens_per_sec: Optional[float] = None

    eval_time_ms: Optional[float] = None
    eval_tokens: Optional[int] = None  # "runs"
    eval_ms_per_token: Optional[float] = None
    eval_tokens_per_sec: Optional[float] = None

    total_time_ms: Optional[float] = None
    total_tokens: Optional[int] = None

    graphs_reused: Optional[int] = None

    # --- legacy-format HTTP response line ---
    http_method: Optional[str] = None
    http_path: Optional[str] = None
    http_status: Optional[int] = None
    client_addr: Optional[str] = None

    @property
    def combined_tokens_per_sec(self) -> Optional[float]:
        if self.total_time_ms and self.total_tokens and self.total_time_ms > 0:
            return round(self.total_tokens / (self.total_time_ms / 1000.0), 2)
        return None

    def is_meaningful(self) -> bool:
        """True if this record actually captured some perf data (as opposed
        to an empty shell created by a stray trigger line)."""
        return any(
            v is not None
            for v in (
                self.load_time_ms,
                self.prompt_eval_time_ms,
                self.eval_time_ms,
                self.total_time_ms,
                self.http_status,
                self.n_tokens_final,
            )
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["combined_tokens_per_sec"] = self.combined_tokens_per_sec
        return d


# --------------------------------------------------------------------------
# Shared perf-line regexes (identical text in both log formats)
# --------------------------------------------------------------------------

RE_LOAD_TIME = re.compile(r"load time\s*=\s*(?P<ms>[\d.]+)\s*ms")

RE_PROMPT_EVAL = re.compile(
    r"prompt eval time\s*=\s*(?P<ms>[\d.]+)\s*ms\s*/\s*(?P<tokens>\d+)\s*tokens\s*"
    r"\(\s*(?P<ms_per_tok>[\d.]+)\s*ms per token,\s*(?P<tps>[\d.]+)\s*tokens per second\)"
)

# NB: only matched on lines that do NOT contain "prompt eval time".
# Legacy (llama-cpp-python) labels the count "runs"; native llama-server
# labels it "tokens" -- accept either.
RE_EVAL = re.compile(
    r"(?<!prompt )eval time\s*=\s*(?P<ms>[\d.]+)\s*ms\s*/\s*(?P<runs>\d+)\s*(?:runs|tokens)\s*"
    r"\(\s*(?P<ms_per_tok>[\d.]+)\s*ms per token,\s*(?P<tps>[\d.]+)\s*tokens per second\)"
)

RE_TOTAL_TIME = re.compile(
    r"total time\s*=\s*(?P<ms>[\d.]+)\s*ms\s*/\s*(?P<tokens>\d+)\s*tokens"
)

RE_GRAPHS_REUSED = re.compile(r"graphs reused\s*=\s*(?P<n>\d+)")


def _fill_perf_fields(rec: RequestRecord, text: str) -> bool:
    """Try each shared perf regex against `text`; fill matching fields on
    `rec`. Returns True if something matched."""
    m = RE_LOAD_TIME.search(text)
    if m and "load time" in text and "prompt eval" not in text and "eval time" not in text:
        rec.load_time_ms = float(m.group("ms"))
        return True

    m = RE_PROMPT_EVAL.search(text)
    if m:
        rec.prompt_eval_time_ms = float(m.group("ms"))
        rec.prompt_eval_tokens = int(m.group("tokens"))
        rec.prompt_eval_ms_per_token = float(m.group("ms_per_tok"))
        rec.prompt_eval_tokens_per_sec = float(m.group("tps"))
        return True

    if "prompt eval time" not in text:
        m = RE_EVAL.search(text)
        if m:
            rec.eval_time_ms = float(m.group("ms"))
            rec.eval_tokens = int(m.group("runs"))
            rec.eval_ms_per_token = float(m.group("ms_per_tok"))
            rec.eval_tokens_per_sec = float(m.group("tps"))
            return True

    m = RE_TOTAL_TIME.search(text)
    if m:
        rec.total_time_ms = float(m.group("ms"))
        rec.total_tokens = int(m.group("tokens"))
        return True

    m = RE_GRAPHS_REUSED.search(text)
    if m:
        rec.graphs_reused = int(m.group("n"))
        return True

    return False


# --------------------------------------------------------------------------
# LEGACY format (llama-cpp-python server via journalctl)
# --------------------------------------------------------------------------

# journalctl -o short-iso line:  2026-07-04T21:56:00-0700 hostname python[172059]: message
RE_LEGACY_LINE_ISO = re.compile(
    r"^(?P<ts>\S+)\s+(?P<host>\S+)\s+(?P<proc>[^\[\s]+)\[(?P<pid>\d+)\]:\s?(?P<msg>.*)$"
)
# journalctl default (short) line:  Jul 04 21:56:00 hostname python[172059]: message
RE_LEGACY_LINE_SHORT = re.compile(
    r"^(?P<ts>\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(?P<host>\S+)\s+"
    r"(?P<proc>[^\[\s]+)\[(?P<pid>\d+)\]:\s?(?P<msg>.*)$"
)

# Single regex to extract the ISO timestamp from a journalctl short-iso line.
# Handles optional timezone offset.
RE_ISO_TS = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2})?)"
)


def _parse_iso_ts(s: str) -> datetime:
    """Parse a short-iso journalctl timestamp to a timezone-aware datetime.

    Handles: ``2026-07-04T21:56:00-0700`` and ``2026-07-04T21:56:00``.
    """
    s = s.strip()
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    # Last resort: remove trailing colon from offset (e.g. -07:00 -> -0700)
    try:
        clean = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", s)
        return datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        raise ValueError(f"Cannot parse ISO timestamp: {s!r}")

RE_CONTEXT_RESET = re.compile(
    r"Llama\.generate:\s*Context reset requested or no prefix match\. Cleared KV cache\."
)
RE_ROLLBACK = re.compile(r"Llama\.generate:\s*Hybrid model rollback triggered\.")
RE_PREFIX_MATCH = re.compile(
    r"Llama\.generate:\s*(?P<hit>\d+)\s*prefix-match hit,\s*remaining\s*(?P<remaining>\d+)\s*prompt tokens to eval"
)

RE_CHECKPOINT = re.compile(
    r"HybridCheckpointCache\(save_checkpoint\):\s*Saved checkpoint at pos\s*(?P<pos>\d+)\s*"
    r"\(\s*(?P<size>[\d.]+)\s*MiB\)\s*total=(?P<total>\d+)\s*used=(?P<used>[\d.]+)\s*MiB"
)

RE_HTTP = re.compile(
    r'INFO:\s*(?P<client>\S+)\s*-\s*"(?P<method>\w+)\s+(?P<path>\S+)\s+HTTP/[\d.]+"\s+(?P<status>\d+)'
)

RE_MODEL_KV = re.compile(r"'([\w.]+)':\s*'([^']*)'")
MODEL_HINT_KEYS = (
    "general.name",
    "general.basename",
    "general.architecture",
    "quantize.imatrix.file",
)


def _legacy_split_line(raw_line: str):
    m = RE_LEGACY_LINE_ISO.match(raw_line)
    if m:
        return m.group("ts"), m.group("pid"), m.group("msg")
    m = RE_LEGACY_LINE_SHORT.match(raw_line)
    if m:
        return m.group("ts"), m.group("pid"), m.group("msg")
    return None, None, raw_line


def _extract_legacy_model_hint(msg: str) -> Optional[str]:
    if "Model metadata:" not in msg:
        return None
    kv = dict(RE_MODEL_KV.findall(msg))
    for key in MODEL_HINT_KEYS:
        if key in kv and kv[key]:
            return kv[key]
    return None


def parse_legacy(text: str) -> List[RequestRecord]:
    records: List[RequestRecord] = []
    current: Optional[RequestRecord] = None
    current_model_hint: Optional[str] = None

    def start_new_record(ts, pid):
        nonlocal current
        if current is not None and current.is_meaningful():
            records.append(current)
        current = RequestRecord(
            log_format="legacy", pid=pid, start_time=ts, model_hint=current_model_hint
        )

    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        ts, pid, msg = _legacy_split_line(raw_line)

        hint = _extract_legacy_model_hint(msg)
        if hint:
            current_model_hint = hint
            if current is not None:
                current.model_hint = current.model_hint or hint

        if RE_CONTEXT_RESET.search(msg):
            start_new_record(ts, pid)
            current.context_reset = True
            continue

        if RE_ROLLBACK.search(msg):
            start_new_record(ts, pid)
            current.rollback_triggered = True
            continue

        m = RE_PREFIX_MATCH.search(msg)
        if m:
            if current is None:
                start_new_record(ts, pid)
            current.prefix_match_hit = int(m.group("hit"))
            current.prompt_tokens_remaining = int(m.group("remaining"))
            continue

        m = RE_CHECKPOINT.search(msg)
        if m:
            if current is None:
                start_new_record(ts, pid)
            current.checkpoints.append(
                Checkpoint(
                    pos=int(m.group("pos")),
                    size_mib=float(m.group("size")),
                    total=int(m.group("total")),
                    used_mib=float(m.group("used")),
                )
            )
            continue

        if "llama_perf_context_print" in msg:
            if current is None:
                start_new_record(ts, pid)
            if _fill_perf_fields(current, msg):
                continue

        m = RE_HTTP.search(msg)
        if m:
            if current is None:
                start_new_record(ts, pid)
            current.client_addr = m.group("client")
            current.http_method = m.group("method")
            current.http_path = m.group("path")
            current.http_status = int(m.group("status"))
            current.end_time = ts
            records.append(current)
            current = None
            continue

    if current is not None and current.is_meaningful():
        records.append(current)

    return records


# --------------------------------------------------------------------------
# NATIVE format (llama-server binary's own logger)
# --------------------------------------------------------------------------

# e.g. "0.00.170.225 I srv  llama_server: n_parallel is set to auto..."
#      "1.01.276.446 I slot      release: id  3 | task 0 | stop processing: ..."
RE_NATIVE_LINE = re.compile(
    r"^(?P<ts>\d+\.\d+\.\d+\.\d+)\s+(?P<level>[IWE])\s+"
    r"(?:(?P<comp>srv|slot)\s+)?(?P<func>[A-Za-z_][\w]*):\s?(?P<msg>.*)$"
)

RE_ID_TASK = re.compile(
    r"^id\s+(?P<slot_id>-?\d+)\s*\|\s*task\s+(?P<task_id>-?\d+)\s*\|\s*(?P<detail>.*)$"
)

RE_LOAD_MODEL_NATIVE = re.compile(r"loading model '(?P<path>[^']+)'")

RE_RELEASE = re.compile(
    r"stop processing:\s*n_tokens\s*=\s*(?P<n_tokens>\d+),\s*truncated\s*=\s*(?P<truncated>\d+)"
)

RE_SELECTED_LRU = re.compile(r"selected slot by LRU, t_last\s*=\s*(?P<t_last>-?\d+)")
RE_SELECTED_LCP = re.compile(
    r"selected slot by LCP similarity, sim_best\s*=\s*(?P<sim>[\d.]+)\s*"
    r"\(>\s*[\d.]+\s*thold\),\s*f_keep\s*=\s*(?P<f_keep>[\d.]+)"
)

RE_CACHE_UPDATE_TOOK = re.compile(r"prompt cache update took\s*(?P<ms>[\d.]+)\s*ms")

RE_CREATE_CHECKPOINT = re.compile(
    r"created context checkpoint\s*(?P<idx>\d+)\s*of\s*(?P<total>\d+)\s*"
    r"\(pos_min\s*=\s*(?P<pos_min>\d+),\s*pos_max\s*=\s*(?P<pos_max>\d+),\s*"
    r"n_tokens\s*=\s*(?P<n_tokens>\d+),\s*size\s*=\s*(?P<size>[\d.]+)\s*MiB\)"
)

RE_FORCE_REPROCESS = re.compile(r"forcing full prompt re-processing due to lack of cache data")
RE_ERASED_CHECKPOINT = re.compile(r"erased invalidated context checkpoint")


def parse_native(text: str) -> List[RequestRecord]:
    records: List[RequestRecord] = []
    open_records: Dict[int, RequestRecord] = {}
    pending_selection: Dict[str, Any] = {}
    current_model_hint: Optional[str] = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Strip journalctl wrapper if present (native logs are embedded)
        inner = line
        m_legacy = RE_LEGACY_LINE_ISO.match(line)
        if m_legacy:
            inner = m_legacy.group("msg")
        else:
            m_legacy = RE_LEGACY_LINE_SHORT.match(line)
            if m_legacy:
                inner = m_legacy.group("msg")

        m = RE_NATIVE_LINE.match(inner)
        if not m:
            # not a timestamped log line (e.g. wrapped chat-template text,
            # shell prompt, CLI args echoed back) -- skip it
            continue

        ts = m.group("ts")
        func = m.group("func")
        msg = m.group("msg")

        # global model identification (applies to the whole server run)
        if func == "load_model":
            mm = RE_LOAD_MODEL_NATIVE.search(msg)
            if mm:
                current_model_hint = mm.group("path").rsplit("/", 1)[-1]
                continue

        m_idtask = RE_ID_TASK.match(msg)
        if not m_idtask:
            # server-level line without id/task, e.g. "prompt cache update took X ms"
            mm = RE_CACHE_UPDATE_TOOK.search(msg)
            if mm:
                pending_selection["cache_update_ms"] = float(mm.group("ms"))
            continue

        slot_id = int(m_idtask.group("slot_id"))
        task_id = int(m_idtask.group("task_id"))
        detail = m_idtask.group("detail")

        if task_id == -1:
            # slot bookkeeping / selection events, not yet tied to a request
            mm = RE_SELECTED_LRU.search(detail)
            if mm:
                pending_selection = {
                    "slot_id": slot_id,
                    "cache_selected_by": "LRU",
                }
                continue
            mm = RE_SELECTED_LCP.search(detail)
            if mm:
                pending_selection = {
                    "slot_id": slot_id,
                    "cache_selected_by": "LCP_similarity",
                    "cache_similarity": float(mm.group("sim")),
                    "cache_f_keep": float(mm.group("f_keep")),
                }
                continue
            # other task=-1 lines (idle slot saving/clearing) -- not needed
            continue

        # task_id >= 0: this belongs to a real request
        if func == "launch_slot_":
            rec = RequestRecord(
                log_format="native",
                start_time=ts,
                model_hint=current_model_hint,
                slot_id=slot_id,
                task_id=task_id,
            )
            if pending_selection:
                rec.cache_selected_by = pending_selection.get("cache_selected_by")
                rec.cache_similarity = pending_selection.get("cache_similarity")
                rec.cache_f_keep = pending_selection.get("cache_f_keep")
                rec.cache_update_ms = pending_selection.get("cache_update_ms")
                pending_selection = {}
            open_records[task_id] = rec
            continue

        rec = open_records.get(task_id)
        if rec is None:
            # robustness: data arrived before we saw a launch_slot_ line
            rec = RequestRecord(
                log_format="native",
                start_time=ts,
                model_hint=current_model_hint,
                slot_id=slot_id,
                task_id=task_id,
            )
            open_records[task_id] = rec

        if func == "create_check":
            mm = RE_CREATE_CHECKPOINT.search(detail)
            if mm:
                rec.context_checkpoints.append(
                    {
                        "index": int(mm.group("idx")),
                        "total": int(mm.group("total")),
                        "pos_min": int(mm.group("pos_min")),
                        "pos_max": int(mm.group("pos_max")),
                        "n_tokens": int(mm.group("n_tokens")),
                        "size_mib": float(mm.group("size")),
                    }
                )
            continue

        if RE_FORCE_REPROCESS.search(detail):
            rec.forced_full_reprocess = True
            continue

        if RE_ERASED_CHECKPOINT.search(detail):
            rec.erased_checkpoint = True
            continue

        if func == "print_timing":
            _fill_perf_fields(rec, detail)
            continue

        if func == "release":
            mm = RE_RELEASE.search(detail)
            if mm:
                rec.n_tokens_final = int(mm.group("n_tokens"))
                rec.truncated = bool(int(mm.group("truncated")))
            rec.end_time = ts
            records.append(rec)
            del open_records[task_id]
            continue

        # other detail lines (e.g. "Checking checkpoint with [...] against N...")
        # aren't needed for the performance summary; ignore.

    # flush any requests that never saw a release line
    for rec in open_records.values():
        if rec.is_meaningful():
            records.append(rec)

    records.sort(key=lambda r: (r.start_time or ""))
    return records


# --------------------------------------------------------------------------
# Format detection + fetching
# --------------------------------------------------------------------------

def detect_format(text: str, sample_lines: int = 50) -> str:
    """Look at the first `sample_lines` non-empty lines and decide whether
    this is 'legacy' or 'native' log output.

    When logs come from journalctl, native-format lines are wrapped in a
    journalctl prefix (ISO timestamp + host + proc[pid]).  We strip that
    prefix first, then check the underlying message."""
    native_hits = 0
    legacy_hits = 0
    checked = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        checked += 1

        # Try to strip a journalctl wrapper to get at the real message
        msg = line
        m = RE_LEGACY_LINE_ISO.match(line)
        if m:
            msg = m.group("msg")
        else:
            m = RE_LEGACY_LINE_SHORT.match(line)
            if m:
                msg = m.group("msg")

        # Check the real message for native format first
        if RE_NATIVE_LINE.match(msg):
            native_hits += 1
        elif RE_NATIVE_LINE.match(line):
            native_hits += 1
        elif RE_LEGACY_LINE_ISO.match(line) or RE_LEGACY_LINE_SHORT.match(line):
            legacy_hits += 1

        if checked >= sample_lines:
            break
    return "native" if native_hits >= legacy_hits else "legacy"


def parse_logs(text: str, fmt: str = "auto") -> List[RequestRecord]:
    if fmt == "auto":
        fmt = detect_format(text)
    if fmt == "native":
        return parse_native(text)
    return parse_legacy(text)


def fetch_logs(
    unit: str = "llamacpp",
    user_scope: bool = True,
    since: Optional[str] = None,
    until: Optional[str] = None,
    lines: Optional[int] = None,
) -> str:
    """Run journalctl and return the raw log text (short-iso format)."""
    cmd = ["journalctl"]
    cmd += ["--user"] if user_scope else []
    cmd += ["-u", unit, "--no-pager", "-o", "short-iso"]
    if since:
        cmd += ["--since", since]
    if until:
        cmd += ["--until", until]
    if lines:
        cmd += ["-n", str(lines)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        print("ERROR: journalctl not found on this system.", file=sys.stderr)
        raise
    except subprocess.CalledProcessError as e:
        print(f"ERROR running journalctl: {e.stderr}", file=sys.stderr)
        raise
    return result.stdout


def parse_window(
    start: datetime,
    end: datetime,
    unit: str = "llamacpp",
    user_scope: bool = True,
    fmt: str = "auto",
) -> list[RequestRecord]:
    """Parse journalctl logs for a specific time window.

    Returns parsed RequestRecords.  journalctl --since/--until filters
    by wall-clock time, so results are already scoped to the window.
    """
    since_str = start.strftime("%Y-%m-%d %H:%M:%S %z")
    until_str = end.strftime("%Y-%m-%d %H:%M:%S %z")
    text = fetch_logs(unit=unit, user_scope=user_scope, since=since_str, until=until_str)
    return parse_logs(text, fmt=fmt)


def find_request_in_window(
    records: list[RequestRecord],
    window_start: datetime,
    window_end: datetime,
) -> RequestRecord | None:
    """Return the RequestRecord that most likely corresponds to the
    benchmark run that occurred between *window_start* and *window_end*.

    Matches by start_time being within the window.  Only works for
    records with parseable ISO timestamps (legacy format).
    If multiple records overlap, returns the one closest to window_start.
    """
    candidates: list[tuple[float, RequestRecord]] = []
    for r in records:
        ts = _parse_record_start(r)
        if ts is not None and window_start <= ts < window_end:
            candidates.append((abs((ts - window_start).total_seconds()), r))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _parse_record_start(rec: RequestRecord) -> datetime | None:
    """Extract the start_time of a record as a timezone-aware datetime."""
    if not rec.start_time:
        return None
    m = RE_ISO_TS.match(rec.start_time)
    if m:
        return _parse_iso_ts(m.group("ts"))
    return None


def _record_in_window(rec: RequestRecord, start: datetime, end: datetime) -> bool:
    """Check if a record's start_time falls within [start, end)."""
    ts = _parse_record_start(rec)
    if ts is None:
        return False
    return start <= ts < end


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

def print_summary(records: List[RequestRecord]) -> None:
    if not records:
        print("No request records found.")
        return

    header = (
        f"{'#':>3} {'start_time':<26} {'model':<24} {'fmt':<7} {'task':>5} "
        f"{'prompt_tok':>10} {'prompt_tps':>10} {'eval_tok':>8} {'eval_tps':>8} "
        f"{'total_ms':>10} {'status/n_tok':>12}"
    )
    print(header)
    print("-" * len(header))
    for i, r in enumerate(records, 1):
        status_col = r.http_status if r.http_status is not None else (
            r.n_tokens_final if r.n_tokens_final is not None else "-"
        )
        print(
            f"{i:>3} "
            f"{(r.start_time or '-'):<26} "
            f"{(r.model_hint or '-')[:24]:<24} "
            f"{(r.log_format or '-'):<7} "
            f"{(r.task_id if r.task_id is not None else '-'):>5} "
            f"{(r.prompt_eval_tokens if r.prompt_eval_tokens is not None else '-'):>10} "
            f"{(r.prompt_eval_tokens_per_sec if r.prompt_eval_tokens_per_sec is not None else '-'):>10} "
            f"{(r.eval_tokens if r.eval_tokens is not None else '-'):>8} "
            f"{(r.eval_tokens_per_sec if r.eval_tokens_per_sec is not None else '-'):>8} "
            f"{(r.total_time_ms if r.total_time_ms is not None else '-'):>10} "
            f"{status_col:>12}"
        )


def write_json(records: List[RequestRecord], path: str) -> None:
    with open(path, "w") as f:
        json.dump([r.to_dict() for r in records], f, indent=2, default=str)


def write_csv(records: List[RequestRecord], path: str) -> None:
    if not records:
        with open(path, "w") as f:
            f.write("")
        return
    rows = [r.to_dict() for r in records]
    # flatten list/nested fields for CSV friendliness
    for row in rows:
        checkpoints = row.pop("checkpoints", [])
        row["checkpoint_count"] = len(checkpoints)
        row["last_checkpoint_pos"] = checkpoints[-1]["pos"] if checkpoints else None

        ctx_checkpoints = row.pop("context_checkpoints", [])
        row["context_checkpoint_count"] = len(ctx_checkpoints)
        row["last_context_checkpoint_pos_max"] = (
            ctx_checkpoints[-1]["pos_max"] if ctx_checkpoints else None
        )
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch and/or parse llama.cpp server journalctl logs into per-request performance records."
    )
    parser.add_argument("--unit", default="llamacpp", help="systemd unit name (default: llamacpp)")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--user", dest="user_scope", action="store_true", default=True,
                        help="use 'journalctl --user' (default)")
    scope.add_argument("--system", dest="user_scope", action="store_false",
                        help="use system-level journalctl instead of --user")

    parser.add_argument("--since", help='e.g. "1 hour ago", "2026-07-04 21:00:00"')
    parser.add_argument("--until", help='e.g. "2026-07-04 22:10:00"')
    parser.add_argument("-n", "--lines", type=int, help="number of lines to fetch (like journalctl -n)")
    parser.add_argument("--input-file", help="parse this log file instead of calling journalctl")
    parser.add_argument(
        "--format", choices=["auto", "legacy", "native"], default="auto",
        help="log format to parse (default: auto-detect)",
    )

    parser.add_argument("--output-json", help="write parsed records to this JSON file")
    parser.add_argument("--output-csv", help="write parsed records to this CSV file")
    parser.add_argument("--no-summary", action="store_true", help="don't print the summary table")

    args = parser.parse_args()

    if args.input_file:
        with open(args.input_file) as f:
            text = f.read()
    else:
        text = fetch_logs(
            unit=args.unit,
            user_scope=args.user_scope,
            since=args.since,
            until=args.until,
            lines=args.lines,
        )

    records = parse_logs(text, fmt=args.format)

    if not args.no_summary:
        print_summary(records)

    if args.output_json:
        write_json(records, args.output_json)
        print(f"\nWrote {len(records)} records to {args.output_json}")

    if args.output_csv:
        write_csv(records, args.output_csv)
        print(f"\nWrote {len(records)} records to {args.output_csv}")


if __name__ == "__main__":
    main()
