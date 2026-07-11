"""Benchmark runner for prompt evaluation.

Usage::

    python -m storyline.benchmark.runner
    python -m storyline.benchmark.runner --translate-models qwen36-35b-a3b
    python -m storyline.benchmark.runner --smoke
    python -m storyline.benchmark.runner --output results.json
    python -m storyline.benchmark.runner --parse-logs --since "1 hour ago"
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import tempfile
import time
import tomllib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from zsp_llm_client.prompt_runner import PromptRunner

from storyline.book.cjk_punct import strip as strip_cjk_punct
from storyline.benchmark import TaskResult, TokenizeValidation, TranslateValidation
from storyline.benchmark.llamacpp_log_parser import (
    RequestRecord,
    fetch_logs,
    parse_logs,
    parse_window,
    find_request_in_window,
)
from storyline.benchmark.token_compare import ComparisonResult, GlobalMetrics, compare_files
from storyline.benchmark.translate_compare import (
    aggregate_translations,
    call_translation_check,
    compute_error_ratio,
    parse_check_response,
    partition_errors_by_block,
)
from storyline.prompt_utils.clean_response import strip_markdown_fences, strip_think_tags
from storyline.services.manager import ServiceManager

_log = logging.getLogger(__name__)

BOOK_DIR = Path("eval")
PROMPT_TRANSLATE = "prompts/translate.md"
PROMPT_TOKENIZE = "prompts/tokenize.txt"
PROMPT_WARMUP = "prompts/warmup.txt"
DEFAULT_CALIBRATION_SUFFIXES = ("_1", "_2")
LOG_FETCH_BUFFER_SEC = 10  # extra time before/after when fetching logs
CSV_LOG_PATH = Path("eval/logs/benchmark_runs.csv")


# ---------------------------------------------------------------------------
# Translation validation
# ---------------------------------------------------------------------------

_REFUSAL_PATTERNS = [
    "i cannot", "unable to", "i'm unable", "can't translate",
    "sorry, i", "i apologize", "as an ai",
]


def validate_translate_output(response: str, expected_count: int | None = None) -> TranslateValidation:
    lines = [l.strip() for l in response.strip().split("\n") if l.strip()]

    parseable = len(lines) > 0
    sentence_count_match = expected_count is None or len(lines) == expected_count
    all_have_chinese = all(
        any("\u4e00" <= ch <= "\u9fff" for ch in line) for line in lines
    )

    lower = response.lower()
    no_refusals = not any(p in lower for p in _REFUSAL_PATTERNS)

    completeness_score = 0.0
    if expected_count and expected_count > 0:
        completeness_score = len(lines) / expected_count

    return TranslateValidation(
        parseable=parseable,
        sentence_count_match=sentence_count_match,
        all_have_chinese=all_have_chinese,
        no_refusals=no_refusals,
        total_sentences=len(lines),
        total_expected=expected_count or 0,
        completeness_score=completeness_score,
    )


# ---------------------------------------------------------------------------
# Tokenize validation
# ---------------------------------------------------------------------------

def _reconstruct_token_texts(line: str) -> str:
    parts: list[str] = []
    for token_str in line.split("||"):
        token_str = token_str.strip()
        if not token_str:
            continue
        parts.append(token_str.split("|", 1)[0])
    return "".join(parts)


def validate_tokenize_output(
    response: str,
    expected_input: list[str],
    case_id: str,
    *,
    book_dir: str | Path = BOOK_DIR,
) -> TokenizeValidation:
    errors: list[str] = []

    lines = [l.strip() for l in response.strip().split("\n") if l.strip()]
    if not lines:
        return TokenizeValidation(
            parseable=False,
            sentence_count_match=False,
            reconstruction_match=False,
            errors=["Empty response"],
        )

    if not any("||" in l for l in lines):
        return TokenizeValidation(
            parseable=False,
            sentence_count_match=False,
            reconstruction_match=False,
            errors=["No || delimiters found in response"],
        )

    parseable = True
    sentence_count_match = True
    reconstruction_match = True

    if len(lines) != len(expected_input):
        sentence_count_match = False
        errors.append(
            f"Sentence count mismatch: expected {len(expected_input)}, got {len(lines)}"
        )

    for i, (line, expected) in enumerate(zip(lines, expected_input)):
        if i >= len(expected_input):
            break
        reconstructed = _reconstruct_token_texts(line)
        if reconstructed != expected:
            reconstruction_match = False
            errors.append(f"Sentence {i} reconstruction mismatch")

    golden_path = Path(book_dir) / f"{case_id}_golden_tokenization.txt"
    golden = None
    if golden_path.exists():
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            ) as f:
                f.write(response)
                tmp_path = f.name
            try:
                golden = compare_files(str(golden_path), tmp_path)
            finally:
                os.unlink(tmp_path)
        except Exception as exc:
            errors.append(f"Golden comparison failed: {exc}")

    return TokenizeValidation(
        parseable=parseable,
        sentence_count_match=sentence_count_match,
        reconstruction_match=reconstruction_match,
        golden=golden,
        errors=errors or None,
    )


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def run_calibration(
    evalset: Sequence[str],
    *,
    book_dir: str | Path = BOOK_DIR,
    suffixes: Sequence[str] = DEFAULT_CALIBRATION_SUFFIXES,
) -> dict[str, dict]:
    calibration: dict[str, dict] = {}
    book_dir = Path(book_dir)

    for case_id in evalset:
        golden_path = book_dir / f"{case_id}_golden_tokenization.txt"
        if not golden_path.exists():
            continue

        variants = sorted(book_dir.glob(f"{case_id}_golden_tokenization_*.txt"))
        if not variants:
            continue

        worst: dict | None = None
        worst_rate = -1.0
        for variant in variants:
            result = compare_files(str(golden_path), str(variant))
            m = result.global_metrics
            entry = {
                "missing_sentences": m.missing_sentences,
                "extra_sentences": m.extra_sentences,
                "content_mismatch_sentences": m.content_mismatch_sentences,
                "missing_tokens": m.missing_tokens,
                "extra_tokens": m.extra_tokens,
                "total_token_errors": m.total_token_errors,
                "token_error_rate": m.token_error_rate,
                "flawless_sentences": m.flawless_sentences,
                "aligned_sentence_pairs": m.aligned_sentence_pairs,
                "pinyin_errors": m.pinyin_errors,
                "pinyin_total": m.pinyin_total,
                "pos_matches": m.pos_matches,
                "pos_mismatches": m.pos_mismatches,
                "pos_total": m.pos_total,
                "pos_error_rate": m.pos_error_rate,
            }
            if m.token_error_rate > worst_rate:
                worst_rate = m.token_error_rate
                worst = entry

        if worst:
            calibration[case_id] = worst

    return calibration


# ---------------------------------------------------------------------------
# Log reconciliation
# ---------------------------------------------------------------------------

def _apply_record(result: TaskResult, rec: RequestRecord) -> None:
    result.prompt_eval_ms = rec.prompt_eval_time_ms
    result.prompt_tokens = rec.prompt_eval_tokens
    result.eval_ms = rec.eval_time_ms
    result.eval_tokens = rec.eval_tokens
    result.total_ms = rec.total_time_ms
    result.total_tokens = rec.total_tokens
    result.prompt_tps = rec.prompt_eval_tokens_per_sec
    result.eval_tps = rec.eval_tokens_per_sec


def _populate_server_metrics(results: list[TaskResult], log_records: list[RequestRecord]) -> None:
    """Mutate each TaskResult in-place with server-side metrics matched from logs.

    For legacy format (ISO timestamps), time-based matching via
    find_request_in_window is used when available.  For native format
    (no absolute timestamps), falls back to sequential matching, skipping
    warmup/trivial records.
    """
    from storyline.benchmark.llamacpp_log_parser import _parse_record_start

    # Separate records into those with ISO timestamps (legacy) and
    # those without (native).  Native records match sequentially.
    native_records = [r for r in log_records if _parse_record_start(r) is None]
    legacy_records = [r for r in log_records if _parse_record_start(r) is not None]

    # For native, skip warmup/trivial calls.
    meaningful_native = [r for r in native_records
                         if r.total_tokens is not None and r.total_tokens > 100]
    native_pool = list(meaningful_native) if meaningful_native else list(native_records)

    for r in results:
        # Try time-based matching against legacy records
        rec = _find_log_record(r, legacy_records)
        if rec is not None:
            _apply_record(r, rec)
            continue
        # Fall back to sequential matching for native records
        if native_pool:
            rec = native_pool.pop(0)
            _apply_record(r, rec)


def _find_log_record(result: TaskResult, log_records: list[RequestRecord]) -> RequestRecord | None:
    """Find the log record that best matches a TaskResult by time proximity.

    Falls back to sequential matching if wall-time metadata is unavailable.
    """
    wall_end = getattr(result, "_wall_end", None)
    wall_start = getattr(result, "_wall_start", None)

    if wall_start is not None and wall_end is not None:
        rec = find_request_in_window(log_records, wall_start, wall_end)
        if rec is None:
            return None
        # Verify the record actually has a parseable ISO time within the window.
        # Native-format records use relative timestamps and will pass through
        # the fallback automatically — those are fine.
        from storyline.benchmark.llamacpp_log_parser import _parse_record_start
        ts = _parse_record_start(rec)
        if ts is not None and not (wall_start <= ts < wall_end):
            return None
        return rec

    return None


def reconcile_logs(
    results: list[TaskResult],
    earliest: datetime,
    latest: datetime,
) -> None:
    """Fetch and parse llamacpp logs for the full benchmark span, then
    populate server-side metrics on every TaskResult."""
    from datetime import timedelta
    # Add a buffer after the latest time — journalctl may not have
    # flushed the last request's log lines yet.
    latest_buffered = latest + timedelta(seconds=LOG_FETCH_BUFFER_SEC)
    earliest_buffered = earliest - timedelta(seconds=LOG_FETCH_BUFFER_SEC)
    try:
        records = parse_window(
            start=earliest_buffered,
            end=latest_buffered,
            unit="llamacpp",
            user_scope=True,
            fmt="auto",
        )
    except Exception:
        _log.warning("Failed to fetch/parse llamacpp logs", exc_info=True)
        return

    if not records:
        _log.info("No llamacpp log records found in window %s - %s", earliest, latest)
        return

    _log.info("Reconciling %d results against %d log records", len(results), len(records))
    _populate_server_metrics(results, records)


# ---------------------------------------------------------------------------
# BenchmarkRunner
# ---------------------------------------------------------------------------

class BenchmarkRunner:
    def __init__(
        self,
        translate_models: Sequence[str] | None = None,
        tokenize_models: Sequence[str] | None = None,
        check_translation_models: Sequence[str] | None = None,
        translate_profile: str | None = None,
        tokenize_profile: str | None = None,
        attempts: int = 1,
        repeat: int = 1,
        evalset: Sequence[str] | None = None,
        smoke: bool = False,
        *,
        service_manager: ServiceManager | None = None,
        parse_logs: bool = True,
    ):
        self.translate_models = list(translate_models or ["local-llamacpp"])
        self.tokenize_models = list(tokenize_models or ["local-llamacpp"])
        self.check_translation_models = list(check_translation_models or ["glm-4.7-flash"])
        self.translate_profile = translate_profile
        self.tokenize_profile = tokenize_profile
        self.attempts = 1 if smoke else attempts
        self.repeat = 1 if smoke else repeat
        self.evalset = list(evalset or (["block_01"] if smoke else [f"block_{i:02d}" for i in range(1, 6)]))
        self.parse_logs = parse_logs
        self.results: list[TaskResult] = []
        self.calibration: dict[str, dict] = {}
        self._svc = service_manager
        self._wall_bounds: list[datetime] = []
        self._warmed_up: bool = False

    @property
    def service_manager(self) -> ServiceManager:
        if self._svc is None:
            self._svc = ServiceManager()
        return self._svc

    # -- public API ----------------------------------------------------------

    def run(self) -> None:
        _log.info("=== Benchmark: translate=%s tokenize=%s attempts=%d repeat=%d ===",
                  self.translate_models, self.tokenize_models, self.attempts, self.repeat)

        for rep in range(1, self.repeat + 1):
            if self.repeat > 1:
                _log.info("--- Repeat %d/%d ---", rep, self.repeat)
            try:
                self.service_manager.ensure_llm_profile(self.translate_profile or self.translate_models[0])
                self._warmup(self.translate_models)
                self._run_translate_phase()
                self._run_tokenize_phase()
            finally:
                self.service_manager.stop("llm")

        if self.parse_logs and self._wall_bounds:
            earliest = min(self._wall_bounds)
            latest = max(self._wall_bounds)
            reconcile_logs(self.results, earliest, latest)

        self._run_translate_check_phase()

    def write_csv_log(self) -> None:
        run_ts = datetime.now(timezone.utc).isoformat()
        t_model = self.translate_profile or (self.translate_models[0] if self.translate_models else "")
        tok_model = self.tokenize_profile or (self.tokenize_models[0] if self.tokenize_models else "")
        chk_model = self.check_translation_models[0] if self.check_translation_models else ""

        fieldnames = [
            "run_timestamp", "translate_model", "tokenize_model", "check_model",
            "attempts",
            "task", "model", "eval_case_id", "attempt",
            "wall_time_s", "valid",
            "prompt_tokens", "eval_tokens", "total_tokens",
            "prompt_eval_s", "eval_s", "total_s",
            "prompt_tps", "eval_tps",
            # translate
            "total_sentences", "check_errors_count", "error_ratio",
            # translate extras
            "total_expected", "completeness_score",
            # tokenize
            "golden_total_token_errors", "golden_token_error_rate",
            "golden_missing_tokens", "golden_extra_tokens",
            "golden_content_mismatch_sentences",
            "golden_missing_sentences", "golden_extra_sentences",
            "golden_flawless_sentences",
            "golden_pinyin_errors", "golden_pinyin_total",
            "golden_pos_mismatches", "golden_pos_matches",
            "golden_pos_error_rate",
            # calibration
            "cal_token_error_rate", "cal_total_token_errors",
            "cal_missing_sentences", "cal_extra_sentences",
            "cal_content_mismatch_sentences",
        ]

        CSV_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_header = CSV_LOG_PATH.stat().st_size == 0
        except FileNotFoundError:
            write_header = True

        with open(CSV_LOG_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()

            for r in self.results:
                row = {
                    "run_timestamp": run_ts,
                    "translate_model": t_model,
                    "tokenize_model": tok_model,
                    "check_model": chk_model,
                    "attempts": self.attempts,
                    "task": r.task,
                    "model": r.model,
                    "eval_case_id": r.eval_case_id,
                    "attempt": r.attempt,
                    "wall_time_s": round(r.wall_time_ms / 1000),
                    "valid": r.valid,
                    "prompt_tokens": r.prompt_tokens or "",
                    "eval_tokens": r.eval_tokens or "",
                    "total_tokens": r.total_tokens or "",
                    "prompt_eval_s": round(r.prompt_eval_ms / 1000) if r.prompt_eval_ms is not None else "",
                    "eval_s": round(r.eval_ms / 1000) if r.eval_ms is not None else "",
                    "total_s": round(r.total_ms / 1000) if r.total_ms is not None else "",
                    "prompt_tps": f"{r.prompt_tps:.2f}" if r.prompt_tps else "",
                    "eval_tps": f"{r.eval_tps:.2f}" if r.eval_tps else "",
                }
                v = r.validation
                if r.task == "translate" and isinstance(v, TranslateValidation):
                    row["total_sentences"] = v.total_sentences
                    row["total_expected"] = v.total_expected
                    row["completeness_score"] = f"{v.completeness_score:.4f}" if v.total_expected else ""
                    row["check_errors_count"] = len(v.check_errors) if v.check_errors else 0
                    row["error_ratio"] = f"{v.error_ratio:.4f}" if v.error_ratio is not None else ""
                elif r.task == "tokenize" and isinstance(v, TokenizeValidation) and v.golden:
                    g = v.golden.global_metrics
                    row["golden_total_token_errors"] = g.total_token_errors
                    row["golden_token_error_rate"] = f"{g.token_error_rate:.4f}"
                    row["golden_missing_tokens"] = g.missing_tokens
                    row["golden_extra_tokens"] = g.extra_tokens
                    row["golden_content_mismatch_sentences"] = g.content_mismatch_sentences
                    row["golden_missing_sentences"] = g.missing_sentences
                    row["golden_extra_sentences"] = g.extra_sentences
                    row["golden_flawless_sentences"] = g.flawless_sentences
                    row["golden_pinyin_errors"] = g.pinyin_errors
                    row["golden_pinyin_total"] = g.pinyin_total
                    row["golden_pos_mismatches"] = g.pos_mismatches
                    row["golden_pos_matches"] = g.pos_matches
                    row["golden_pos_error_rate"] = f"{g.pos_error_rate:.4f}"
                writer.writerow(row)

            # calibration rows
            for case_id in self.evalset:
                cal = self.calibration.get(case_id)
                if cal is None:
                    continue
                writer.writerow({
                    "run_timestamp": run_ts,
                    "translate_model": t_model,
                    "tokenize_model": tok_model,
                    "check_model": chk_model,
                    "attempts": self.attempts,
                    "task": "calibration",
                    "eval_case_id": case_id,
                    "cal_token_error_rate": cal.get("token_error_rate", ""),
                    "cal_total_token_errors": cal.get("total_token_errors", ""),
                    "cal_missing_sentences": cal.get("missing_sentences", ""),
                    "cal_extra_sentences": cal.get("extra_sentences", ""),
                    "cal_content_mismatch_sentences": cal.get("content_mismatch_sentences", ""),
                })

        _log.info("CSV log appended to %s", CSV_LOG_PATH)

    def print_report(self) -> None:
        print("\n" + "=" * 70)
        print("BENCHMARK REPORT")
        print("=" * 70)

        for task in ("translate", "tokenize"):
            group = [r for r in self.results if r.task == task]
            header = "Translate" if task == "translate" else "Tokenize"
            print(f"\n--- {header} ({len(group)} runs) ---")
            if not group:
                print("  (no results)")
                continue
            valid = sum(1 for r in group if r.valid)
            avg_wall = sum(r.wall_time_ms for r in group) / len(group)
            total_server_tokens = sum(r.total_tokens or 0 for r in group)
            print(f"  Valid: {valid}/{len(group)}")
            print(f"  Avg wall time: {avg_wall:.0f}ms")
            if total_server_tokens:
                print(f"  Total server tokens: {total_server_tokens}")
            for r in group:
                status = "OK" if r.valid else "FAIL"
                server_info = ""
                if r.prompt_tokens is not None:
                    server_info += f" prompt={r.prompt_tokens}tok/{r.prompt_eval_ms:.0f}ms"
                if r.eval_tokens is not None:
                    server_info += f" eval={r.eval_tokens}tok/{r.eval_ms:.0f}ms"
                suffix = ""
                if task == "tokenize":
                    v = r.validation
                    if isinstance(v, TokenizeValidation) and v.golden:
                        g = v.golden.global_metrics
                        suffix = f" | golden: {g.total_token_errors} tok-errs rate={g.token_error_rate:.3f}"
                        if g.content_mismatch_sentences:
                            suffix += f" content-mis={g.content_mismatch_sentences}"
                        if g.pos_total:
                            suffix += f" pinyin-errs={g.pinyin_errors}/{g.pinyin_total}"
                            suffix += f" pos-errs={g.pos_mismatches}/{g.pos_total} pos-rate={g.pos_error_rate:.3f}"
                elif task == "translate":
                    v = r.validation
                    if isinstance(v, TranslateValidation):
                        suffix = f" | {v.total_sentences} sents"
                        if not v.sentence_count_match and v.total_expected:
                            suffix += f" (exp: {v.total_expected})"
                        if v.check_errors:
                            suffix += f" check_errs={len(v.check_errors)}"
                        if v.error_ratio is not None:
                            suffix += f" ratio={v.error_ratio:.3f}"
                        if v.completeness_score < 1.0:
                            suffix += f" complete={v.completeness_score:.0%}"
                print(f"  {r.eval_case_id} #{r.attempt}: {status} "
                      f"wall={r.wall_time_ms:.0f}ms{server_info}{suffix}")

        if self.calibration:
            print(f"\n--- Calibration ---")
            for case_id, m in self.calibration.items():
                parts = [f"{m['total_token_errors']} tok-errs rate={m['token_error_rate']:.3f}"]
                if m.get('content_mismatch_sentences'):
                    parts.append(f"content-mis={m['content_mismatch_sentences']}")
                if m.get('pinyin_errors') is not None:
                    parts.append(f"pinyin-errs={m['pinyin_errors']}/{m['pinyin_total']}")
                if m.get('pos_mismatches') is not None:
                    parts.append(f"pos-errs={m['pos_mismatches']}/{m['pos_total']} pos-rate={m['pos_error_rate']:.3f}")
                print(f"  {case_id}: " + " ".join(parts))

        print()

    def to_json(self) -> dict:
        def _convert(obj):
            if hasattr(obj, "__dataclass_fields__"):
                d = {}
                for k in obj.__dataclass_fields__:
                    if k.startswith("_"):
                        continue
                    v = getattr(obj, k)
                    if isinstance(v, ComparisonResult):
                        v = {"global_metrics": _convert(v.global_metrics)}
                    else:
                        v = _convert(v)
                    d[k] = v
                return d
            if isinstance(obj, GlobalMetrics):
                return asdict(obj)
            if isinstance(obj, list):
                return [_convert(x) for x in obj]
            if isinstance(obj, dict):
                return {k: _convert(v) for k, v in obj.items()}
            return obj

        return {
            "config": {
                "models": {
                    "translate": self.translate_models,
                    "tokenize": self.tokenize_models,
                },
                "attempts": self.attempts,
                "evalset": self.evalset,
            },
            "calibration": self.calibration,
            "results": _convert(self.results),
        }

    # -- phases --------------------------------------------------------------

    def _run_translate_phase(self) -> None:
        model = self.translate_profile or self.translate_models[0]
        _log.info("--- Translate phase (%s) ---", model)

        for case_id in self.evalset:
            source_path = BOOK_DIR / f"{case_id}.txt"
            source_text = source_path.read_text(encoding="utf-8")
            expected = len([l for l in source_text.strip().split("\n") if l.strip()])

            for attempt in range(1, self.attempts + 1):
                _log.info("  translate %s #%d", case_id, attempt)
                wall_start, response, wall_end = self._timed_llm_call(
                    PROMPT_TRANSLATE, source_text, self.translate_models
                )
                elapsed = (wall_end - wall_start).total_seconds() * 1000

                if response is None:
                    tr = TaskResult(
                        task="translate", model=model, eval_case_id=case_id,
                        attempt=attempt, wall_time_ms=elapsed,
                        response_chars=0, valid=False,
                        _response_raw="",
                        validation=TranslateValidation(
                            parseable=False, sentence_count_match=False,
                            all_have_chinese=False, no_refusals=False,
                        ),
                    )
                    tr._wall_start = wall_start  # type: ignore[attr-defined]
                    tr._wall_end = wall_end      # type: ignore[attr-defined]
                    self.results.append(tr)
                    continue

                validation = validate_translate_output(response, expected)
                valid = all([
                    validation.parseable,
                    validation.all_have_chinese,
                    validation.no_refusals,
                ])

                tr = TaskResult(
                    task="translate", model=model, eval_case_id=case_id,
                    attempt=attempt, wall_time_ms=elapsed,
                    response_chars=len(response), valid=valid,
                    _response_raw=response,
                    validation=validation,
                )
                tr._wall_start = wall_start  # type: ignore[attr-defined]
                tr._wall_end = wall_end      # type: ignore[attr-defined]
                self.results.append(tr)
                extra = ""
                if not validation.sentence_count_match:
                    extra = f" incomplete=%.0f%%" % (validation.completeness_score * 100)
                _log.info("    -> %d/%d sents %dc %.0fms valid=%s%s",
                          validation.total_sentences, validation.total_expected,
                          len(response), elapsed, valid, extra)

    def _run_tokenize_phase(self) -> None:
        model = self.tokenize_profile or self.tokenize_models[0]
        _log.info("--- Tokenize phase (%s) ---", model)

        # Switch profile if tokenize profile differs from translate
        if self.tokenize_profile and self.tokenize_profile != self.translate_profile:
            self.service_manager.ensure_llm_profile(self.tokenize_profile or model)

        for case_id in self.evalset:
            translation_path = BOOK_DIR / f"{case_id}_golden_translation.txt"
            if not translation_path.exists():
                _log.warning("  no golden translation for %s, skip", case_id)
                continue

            chinese_text = translation_path.read_text(encoding="utf-8")
            chinese_lines = [l.strip() for l in chinese_text.strip().split("\n") if l.strip()]
            stripped = []
            for line in chinese_lines:
                s, _ = strip_cjk_punct(line)
                stripped.append(s)
            tokenize_input = "\n".join(stripped)

            for attempt in range(1, self.attempts + 1):
                _log.info("  tokenize %s #%d", case_id, attempt)
                wall_start, response, wall_end = self._timed_llm_call(
                    PROMPT_TOKENIZE, tokenize_input, self.tokenize_models
                )
                elapsed = (wall_end - wall_start).total_seconds() * 1000

                if response is None:
                    tr = TaskResult(
                        task="tokenize", model=model, eval_case_id=case_id,
                        attempt=attempt, wall_time_ms=elapsed,
                        response_chars=0, valid=False,
                        validation=TokenizeValidation(
                            parseable=False, sentence_count_match=False,
                            reconstruction_match=False,
                            errors=["LLM call failed"],
                        ),
                    )
                    tr._wall_start = wall_start  # type: ignore[attr-defined]
                    tr._wall_end = wall_end      # type: ignore[attr-defined]
                    self.results.append(tr)
                    continue

                validation = validate_tokenize_output(response, stripped, case_id)
                tr = TaskResult(
                    task="tokenize", model=model, eval_case_id=case_id,
                    attempt=attempt, wall_time_ms=elapsed,
                    response_chars=len(response), valid=validation.parseable,
                    validation=validation,
                )
                tr._wall_start = wall_start  # type: ignore[attr-defined]
                tr._wall_end = wall_end      # type: ignore[attr-defined]
                self.results.append(tr)

                ginfo = ""
                if validation.golden:
                    g = validation.golden.global_metrics
                    ginfo = f" golden:{g.total_token_errors}errs rate={g.token_error_rate:.3f}"
                _log.info("    -> %dc %.0fms valid=%s%s",
                          len(response), elapsed, validation.parseable, ginfo)

        self.calibration = run_calibration(self.evalset)
        if self.calibration:
            _log.info("  calibration: %s",
                      {c: m["token_error_rate"] for c, m in self.calibration.items()})

    def _run_translate_check_phase(self) -> None:
        translate_results = [r for r in self.results
                             if r.task == "translate" and r.valid and r.response_chars > 0]
        if not translate_results:
            _log.info("--- Translate check: no valid translate results to check ---")
            return

        _log.info("--- Translate check phase (%s) ---", self.check_translation_models)

        aggregated_text, block_ranges = aggregate_translations(translate_results)
        if not aggregated_text.strip():
            _log.info("  empty aggregated text, skipping check")
            return

        total_sentences = len(aggregated_text.strip().split("\n"))
        _log.info("  aggregated %d sentences across %d results", total_sentences, len(translate_results))

        try:
            response = call_translation_check(aggregated_text, self.check_translation_models)
        except Exception as exc:
            _log.error("  translation check call failed: %s", exc)
            return

        errors = parse_check_response(response)
        error_ratio = compute_error_ratio(errors, total_sentences)
        _log.info("  check returned %d errors (ratio=%.4f)", len(errors), error_ratio)

        per_block_errors = partition_errors_by_block(errors, block_ranges)

        for tr, block_errs in zip(translate_results, per_block_errors):
            if isinstance(tr.validation, TranslateValidation):
                tr.validation.check_errors = block_errs
                tr.validation.error_ratio = error_ratio
                tr.validation.total_sentences = tr.validation.total_sentences  # keep existing

    # -- internals -----------------------------------------------------------

    def _warmup(self, models: list[str]) -> None:
        if self._warmed_up:
            return
        _log.info("  warmup...")
        self._call_llm(PROMPT_WARMUP, "Hello.", models)
        self._warmed_up = True
        _log.info("  warmup done")

    def _timed_llm_call(
        self, prompt_path: str, input_text: str, models: list[str]
    ) -> tuple[datetime, str | None, datetime]:
        """Call the LLM, returning (wall_start, response_or_None, wall_end)."""
        wall_start = datetime.now(timezone.utc)
        try:
            response = self._call_llm(prompt_path, input_text, models)
        except Exception as exc:
            _log.error("LLM call failed: %s", exc)
            response = None
        wall_end = datetime.now(timezone.utc)

        if self.parse_logs:
            # Extend bounds with a buffer to catch log entries
            self._wall_bounds.append(wall_start)
            self._wall_bounds.append(wall_end)

        return wall_start, response, wall_end

    @staticmethod
    def _call_llm(prompt_path: str, input_text: str, models: list[str]) -> str:
        runner = PromptRunner()
        response = runner.run(prompt_path, input_text, models=models)
        if response is None:
            raise RuntimeError(f"PromptRunner returned None for {prompt_path!r}")
        response = strip_think_tags(response)
        response = strip_markdown_fences(response)
        return response


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_config() -> tuple[list[str], list[str], list[str], str | None, str | None, int]:
    config_path = Path(__file__).resolve().parent.parent / "config" / "llms_for_tasks.toml"
    try:
        with config_path.open("rb") as f:
            cfg = tomllib.load(f)
        llm_cfg = cfg.get("llm", {})
        model_id = llm_cfg.get("model_id", "local-llamacpp")
        default_profile = cfg.get("default", {}).get("profile")
        translate_profile = cfg.get("translate", {}).get("profile", default_profile)
        tokenize_profile = cfg.get("tokenize", {}).get("profile", default_profile)
        cfg_check = cfg.get("check_translation", {}).get("models", ["glm-4.7-flash"])
        repeat = cfg.get("benchmark", {}).get("repeat", 1)
        return ([model_id], [model_id], cfg_check, translate_profile, tokenize_profile, repeat)
    except Exception:
        return (["local-llamacpp"], ["local-llamacpp"], ["glm-4.7-flash"], None, None, 1)



def main() -> None:
    logging.basicConfig(format="%(asctime)s %(message)s", datefmt="%H:%M:%S", level=logging.INFO)

    cfg_translate, cfg_tokenize, cfg_check, translate_profile, tokenize_profile, cfg_repeat = _load_config()

    parser = argparse.ArgumentParser(description="Benchmark LLM prompt performance")
    parser.add_argument("--translate-models", type=str, default=None,
                        help="Override translate models (comma-separated)")
    parser.add_argument("--tokenize-models", type=str, default=None,
                        help="Override tokenize models (comma-separated)")
    parser.add_argument("--check-translation-models", type=str, default=None,
                        help="Override translation check models (comma-separated)")
    parser.add_argument("--smoke", action="store_true",
                        help="Quick smoke test: 1 attempt, 1 block")
    parser.add_argument("--output", type=str, default=None,
                        help="Output results to JSON file")
    parser.add_argument("--attempts", type=int, default=1,
                        help="Number of attempts per block (default: 1)")
    parser.add_argument("--repeat", type=int, default=cfg_repeat,
                        help="Number of full benchmark repetitions (default: 1)")
    parser.add_argument("--parse-logs", dest="parse_logs", action="store_true", default=True,
                        help="Fetch and reconcile server-side metrics from journalctl "
                             "(default: True; use --no-parse-logs to skip)")
    parser.add_argument("--no-parse-logs", dest="parse_logs", action="store_false",
                        help="Skip log parsing")
    parser.add_argument("--since", type=str, default=None,
                        help="Parse logs since (used with --no-run)")
    parser.add_argument("--no-run", action="store_true",
                        help="Parse logs only (no LLM calls)")
    args = parser.parse_args()

    translate_models = cfg_translate
    if args.translate_models:
        translate_models = [m.strip() for m in args.translate_models.split(",")]

    tokenize_models = cfg_tokenize
    if args.tokenize_models:
        tokenize_models = [m.strip() for m in args.tokenize_models.split(",")]

    check_translation_models = cfg_check
    if args.check_translation_models:
        check_translation_models = [m.strip() for m in args.check_translation_models.split(",")]

    if args.no_run:
        # Parse logs only mode
        if not args.since:
            print("ERROR: --no-run requires --since", file=__import__("sys").stderr)
            raise SystemExit(1)
        _reconcile_standalone(args.since)
        return

    runner = BenchmarkRunner(
        translate_models=translate_models,
        tokenize_models=tokenize_models,
        check_translation_models=check_translation_models,
        translate_profile=translate_profile,
        tokenize_profile=tokenize_profile,
        attempts=args.attempts,
        repeat=args.repeat,
        smoke=args.smoke,
        parse_logs=args.parse_logs,
    )
    runner.run()
    runner.print_report()
    runner.write_csv_log()

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(runner.to_json(), f, indent=2, ensure_ascii=False)
        print(f"Results written to {args.output}")


def _reconcile_standalone(since: str) -> None:
    """Fetch logs since *since* and print a summary (--parse-logs --since mode)."""
    text = fetch_logs(unit="llamacpp", user_scope=True, since=since)
    records = parse_logs(text, fmt="auto")
    if not records:
        print("No request records found.")
        return
    from storyline.benchmark.llamacpp_log_parser import print_summary
    print_summary(records)


if __name__ == "__main__":
    main()