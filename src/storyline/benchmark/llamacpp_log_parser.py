#!/usr/bin/env python3
"""
llamacpp_log_parser.py

Fetch and parse journalctl logs from the compiled llama-server binary
into per-request performance records.

The native format looks like:
  "0.00.170.225 I srv  llama_server: message"
  "1.01.276.390 I slot print_timing: id 3 | task 0 | ..."

Requests are tracked per (slot id, task id), bounded by "launch_slot_"
and "release" lines.

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
from typing import List, Optional, Dict, Any


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class RequestRecord:
    log_format: Optional[str] = None  # "native"
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    # model info (best-effort)
    model_hint: Optional[str] = None

    # slot/task tracking
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

    # shared perf fields (llama_perf_context_print / print_timing)
    load_time_ms: Optional[float] = None

    prompt_eval_time_ms: Optional[float] = None
    prompt_eval_tokens: Optional[int] = None
    prompt_eval_ms_per_token: Optional[float] = None
    prompt_eval_tokens_per_sec: Optional[float] = None

    eval_time_ms: Optional[float] = None
    eval_tokens: Optional[int] = None  # "runs" or "tokens"
    eval_ms_per_token: Optional[float] = None
    eval_tokens_per_sec: Optional[float] = None

    total_time_ms: Optional[float] = None
    total_tokens: Optional[int] = None

    graphs_reused: Optional[int] = None

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
                self.n_tokens_final,
            )
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["combined_tokens_per_sec"] = self.combined_tokens_per_sec
        return d


# --------------------------------------------------------------------------
# Shared perf-line regexes
# --------------------------------------------------------------------------

RE_LOAD_TIME = re.compile(r"load time\s*=\s*(?P<ms>[\d.]+)\s*ms")

RE_PROMPT_EVAL = re.compile(
    r"prompt eval time\s*=\s*(?P<ms>[\d.]+)\s*ms\s*/\s*(?P<tokens>\d+)\s*tokens\s*"
    r"\(\s*(?P<ms_per_tok>[\d.]+)\s*ms per token,\s*(?P<tps>[\d.]+)\s*tokens per second\)"
)

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
# NATIVE format (llama-server binary's own logger)
# --------------------------------------------------------------------------

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
    r"selected slot by LCP similarity, f_sim_best\s*=\s*(?P<sim>[\d.]+)\s*"
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

        m = RE_NATIVE_LINE.match(line)
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
# Public API
# --------------------------------------------------------------------------

def parse_logs(text: str) -> List[RequestRecord]:
    return parse_native(text)


def fetch_logs(
    unit: str = "llamacpp",
    user_scope: bool = True,
    since: Optional[str] = None,
    until: Optional[str] = None,
    lines: Optional[int] = None,
) -> str:
    """Run journalctl and return the raw log text (cat format — no prefix).

    We use ``-o cat`` (message-only) so that native-format timestamps
    (e.g. ``1.44.356.190``) appear at the start of each line, matching
    the parser's ``RE_NATIVE_LINE``.  journalctl's ``--since`` / ``--until``
    still filter by wall-clock time at the source.
    """
    cmd = ["journalctl"]
    cmd += ["--user"] if user_scope else []
    cmd += ["-u", unit, "--no-pager", "-o", "cat"]
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
) -> list[RequestRecord]:
    """Parse journalctl logs for a specific time window.

    Returns parsed RequestRecords.  journalctl --since/--until filters
    by wall-clock time, so results are already scoped to the window.
    """
    since_str = start.strftime("%Y-%m-%d %H:%M:%S %z")
    until_str = end.strftime("%Y-%m-%d %H:%M:%S %z")
    text = fetch_logs(unit=unit, user_scope=user_scope, since=since_str, until=until_str)
    return parse_logs(text)


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

def print_summary(records: List[RequestRecord]) -> None:
    if not records:
        print("No request records found.")
        return

    header = (
        f"{'#':>3} {'start_time':<20} {'model':<24} {'task':>5} "
        f"{'prompt_tok':>10} {'prompt_tps':>10} {'eval_tok':>8} {'eval_tps':>8} "
        f"{'total_ms':>10} {'n_tok_final':>12}"
    )
    print(header)
    print("-" * len(header))
    for i, r in enumerate(records, 1):
        print(
            f"{i:>3} "
            f"{(r.start_time or '-'):<20} "
            f"{(r.model_hint or '-')[:24]:<24} "
            f"{(r.task_id if r.task_id is not None else '-'):>5} "
            f"{(r.prompt_eval_tokens if r.prompt_eval_tokens is not None else '-'):>10} "
            f"{(r.prompt_eval_tokens_per_sec if r.prompt_eval_tokens_per_sec is not None else '-'):>10} "
            f"{(r.eval_tokens if r.eval_tokens is not None else '-'):>8} "
            f"{(r.eval_tokens_per_sec if r.eval_tokens_per_sec is not None else '-'):>8} "
            f"{(r.total_time_ms if r.total_time_ms is not None else '-'):>10} "
            f"{(r.n_tokens_final if r.n_tokens_final is not None else '-'):>12}"
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

    records = parse_logs(text)

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