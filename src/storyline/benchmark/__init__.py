"""Benchmark data model types shared across modules."""

from __future__ import annotations

from dataclasses import dataclass

from storyline.benchmark.token_compare import ComparisonResult


@dataclass
class TranslateValidation:
    parseable: bool
    sentence_count_match: bool
    all_have_chinese: bool
    no_refusals: bool
    check_errors: list[str] | None = None
    error_ratio: float | None = None
    total_sentences: int = 0
    total_expected: int = 0
    completeness_score: float = 0.0


@dataclass
class TokenizeValidation:
    parseable: bool
    sentence_count_match: bool
    reconstruction_match: bool
    golden: ComparisonResult | None = None
    calibration: ComparisonResult | None = None
    errors: list[str] | None = None


@dataclass
class TaskResult:
    task: str
    model: str
    eval_case_id: str
    attempt: int
    wall_time_ms: float
    response_chars: int
    # Server-side metrics from llamacpp logs (None if unavailable)
    prompt_eval_ms: float | None = None
    prompt_tokens: int | None = None
    eval_ms: float | None = None
    eval_tokens: int | None = None
    total_ms: float | None = None
    total_tokens: int | None = None
    # Perf extras
    prompt_tps: float | None = None
    eval_tps: float | None = None
    valid: bool = True
    validation: TranslateValidation | TokenizeValidation | None = None
    # Private: raw response text for aggregation
    _response_raw: str = ""