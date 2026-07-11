"""Tests for storyline.benchmark.runner."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from datetime import datetime, timezone

from storyline.benchmark import TaskResult, TranslateValidation, TokenizeValidation
from storyline.benchmark.runner import (
    BenchmarkRunner,
    validate_translate_output,
    validate_tokenize_output,
    run_calibration,
    _load_config,
    _populate_server_metrics,
    _find_log_record,
    reconcile_logs,
    _reconstruct_token_texts,
)
from storyline.benchmark.llamacpp_log_parser import RequestRecord


# ---------------------------------------------------------------------------
# Translate validation
# ---------------------------------------------------------------------------

class TestValidateTranslateOutput:
    def test_valid_output(self):
        v = validate_translate_output("你好\n世界\n", 2)
        assert v.parseable is True
        assert v.sentence_count_match is True
        assert v.all_have_chinese is True
        assert v.no_refusals is True
        assert v.total_sentences == 2

    def test_empty_response(self):
        v = validate_translate_output("", 1)
        assert v.parseable is False
        assert v.total_sentences == 0

    def test_sentence_count_mismatch(self):
        v = validate_translate_output("你好\n世界\n", 3)
        assert v.sentence_count_match is False
        assert v.total_sentences == 2

    def test_no_expected_count(self):
        v = validate_translate_output("a\nb\n", None)
        assert v.sentence_count_match is True

    def test_no_chinese(self):
        v = validate_translate_output("hello\nworld", 2)
        assert v.all_have_chinese is False

    def test_refusal_detected(self):
        v = validate_translate_output("I cannot translate this.", 1)
        assert v.no_refusals is False

    def test_refusal_sorry(self):
        v = validate_translate_output("Sorry, I can't help.", 1)
        assert v.no_refusals is False

    def test_blank_lines_stripped(self):
        v = validate_translate_output("\n\n你好\n\n世界\n\n", 2)
        assert v.total_sentences == 2
        assert v.sentence_count_match is True


# ---------------------------------------------------------------------------
# Tokenize validation
# ---------------------------------------------------------------------------

class TestReconstructTokenTexts:
    def test_simple(self):
        assert _reconstruct_token_texts("我|wǒ|r||是|shì|v") == "我是"

    def test_compound(self):
        assert _reconstruct_token_texts("担心|dānxīn|v") == "担心"

    def test_punctuation(self):
        assert _reconstruct_token_texts("。|w") == "。"

    def test_english(self):
        assert _reconstruct_token_texts("BooBoo|BooBoo|nr") == "BooBoo"


class TestValidateTokenizeOutput:
    def test_valid_output(self):
        response = "这|zhè|r||是|shì|v\nBooBoo|BooBoo|nr"
        expected = ["这是", "BooBoo"]
        v = validate_tokenize_output(response, expected, "test_block")
        assert v.parseable is True
        assert v.sentence_count_match is True
        assert v.reconstruction_match is True
        assert v.errors is None

    def test_empty_response(self):
        v = validate_tokenize_output("", ["这是"], "test_block")
        assert v.parseable is False
        assert v.errors == ["Empty response"]

    def test_no_delimiters(self):
        v = validate_tokenize_output("我wǒr是你shìv", ["我是你"], "test_block")
        assert v.parseable is False
        assert v.errors == ["No || delimiters found in response"]

    def test_sentence_count_mismatch(self):
        response = "我|wǒ|r||是|shì|v"
        expected = ["我是", "你好"]
        v = validate_tokenize_output(response, expected, "test_block")
        assert v.parseable is True
        assert v.sentence_count_match is False
        assert v.errors is not None
        assert any("Sentence count mismatch" in e for e in v.errors)

    def test_reconstruction_mismatch(self):
        response = "我|wǒ|r||是|shì|v"
        expected = ["你好"]
        v = validate_tokenize_output(response, expected, "test_block")
        assert v.reconstruction_match is False
        assert any("reconstruction mismatch" in e for e in (v.errors or []))

    @patch("storyline.benchmark.runner.compare_files")
    def test_golden_comparison(self, mock_compare, tmp_path):
        golden_dir = tmp_path / "eval"
        golden_dir.mkdir()
        golden_file = golden_dir / "test_golden_golden_tokenization.txt"
        golden_file.write_text("我|wǒ|r||是|shì|v\n")

        from storyline.benchmark.token_compare import ComparisonResult, ComparisonMeta, GlobalMetrics
        mock_result = ComparisonResult(
            meta=ComparisonMeta("a", "b", 1, 1, 2, 2),
            global_metrics=GlobalMetrics(1, 0, 0, 1, 0, 0, 0, 0, 0.0),
        )
        mock_compare.return_value = mock_result

        v = validate_tokenize_output(
            "我|wǒ|r||是|shì|v", ["我是"], "test_golden",
            book_dir=str(golden_dir),
        )
        assert v.golden is not None
        assert v.golden.global_metrics.total_token_errors == 0

    def test_golden_missing_file(self):
        v = validate_tokenize_output(
            "我|wǒ|r||是|shì|v", ["我是"], "nonexistent",
            book_dir="/tmp/nonexistent_eval_dir",
        )
        assert v.golden is None


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

class TestRunCalibration:
    def test_no_variants(self, tmp_path):
        cal = run_calibration(["block_01"], book_dir=str(tmp_path))
        assert cal == {}

    def test_with_variants(self, tmp_path):
        golden = tmp_path / "block_01_golden_tokenization.txt"
        golden.write_text("我|wǒ|r||是|shì|v\n你|nǐ|r\n")

        v1 = tmp_path / "block_01_golden_tokenization_1.txt"
        v1.write_text("我|wǒ|r||是|shì|v\n你|nǐ|r||好|hǎo|a\n")

        cal = run_calibration(["block_01"], book_dir=str(tmp_path))
        assert "block_01" in cal
        assert cal["block_01"]["total_token_errors"] > 0

    def test_golden_missing(self, tmp_path):
        cal = run_calibration(["nonexistent"], book_dir=str(tmp_path))
        assert cal == {}


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_loads_defaults(self):
        with patch("storyline.benchmark.runner.Path.open", side_effect=FileNotFoundError):
            t, tok, check, _, _, repeat = _load_config()
            assert t == ["local-llamacpp"]
            assert tok == ["local-llamacpp"]
            assert check == ["glm-4.7-flash"]
            assert repeat == 1


# ---------------------------------------------------------------------------
# TaskResult / data model
# ---------------------------------------------------------------------------

class TestTaskResult:
    def test_translate_result(self):
        r = TaskResult(
            task="translate", model="qwen", eval_case_id="block_01",
            attempt=1, wall_time_ms=123.4, response_chars=50, valid=True,
            validation=TranslateValidation(
                parseable=True, sentence_count_match=True,
                all_have_chinese=True, no_refusals=True, total_sentences=3,
            ),
        )
        assert r.task == "translate"
        assert r.valid is True
        assert isinstance(r.validation, TranslateValidation)
        assert r.validation.total_sentences == 3

    def test_tokenize_result(self):
        r = TaskResult(
            task="tokenize", model="qwen", eval_case_id="block_01",
            attempt=1, wall_time_ms=456.7, response_chars=200, valid=True,
            validation=TokenizeValidation(
                parseable=True, sentence_count_match=True,
                reconstruction_match=True, errors=None,
            ),
        )
        assert r.task == "tokenize"
        assert r.valid is True


# ---------------------------------------------------------------------------
# BenchmarkRunner (unit, with mocked LLM)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_svc():
    svc = MagicMock()
    return svc


@pytest.fixture
def eval_fixture(tmp_path):
    d = tmp_path / "eval"
    d.mkdir()
    (d / "block_01.txt").write_text("Hello.\nWorld.\n")
    (d / "block_01_golden_translation.txt").write_text("你好\n世界\n")
    (d / "block_01_golden_tokenization.txt").write_text("你|nǐ|r||好|hǎo|a\n世界|shìjiè|n\n")
    return d


@pytest.fixture
def runner_factory(mock_svc, eval_fixture, monkeypatch):
    from storyline.benchmark import runner as rmod

    monkeypatch.setattr(rmod, "BOOK_DIR", eval_fixture)
    monkeypatch.setattr(rmod, "PROMPT_TRANSLATE", "prompts/translate.md")
    monkeypatch.setattr(rmod, "PROMPT_TOKENIZE", "prompts/tokenize.txt")
    monkeypatch.setattr(rmod, "PROMPT_WARMUP", "prompts/warmup.txt")

    def _make(**kwargs):
        return BenchmarkRunner(
            service_manager=mock_svc,
            **kwargs,
        )
    return _make


class TestBenchmarkRunnerSmoke:
    def test_smoke_mode_limits(self, runner_factory):
        r = runner_factory(smoke=True)
        assert r.attempts == 1
        assert r.evalset == ["block_01"]

    def test_translate_phase(self, runner_factory, mock_svc):
        r = runner_factory(smoke=True)

        with patch.object(r, "_call_llm") as mock_llm:
            mock_llm.return_value = "你好\n世界\n"
            r._run_translate_phase()

        assert len(r.results) == 1
        res = r.results[0]
        assert res.task == "translate"
        assert res.valid is True
        v = res.validation
        assert isinstance(v, TranslateValidation)
        assert v.total_sentences == 2

    def test_translate_phase_failure(self, runner_factory, mock_svc):
        r = runner_factory(smoke=True)

        with patch.object(r, "_warmup"):
            with patch.object(r, "_call_llm", side_effect=RuntimeError("boom")):
                r._run_translate_phase()

        assert len(r.results) == 1
        assert r.results[0].valid is False

    def test_tokenize_phase(self, runner_factory, mock_svc):
        r = runner_factory(smoke=True)

        with patch.object(r, "_call_llm") as mock_llm:
            mock_llm.return_value = "你|nǐ|r||好|hǎo|a\n世界|shìjiè|n\n"
            r._run_tokenize_phase()

        assert len(r.results) == 1
        res = r.results[0]
        assert res.task == "tokenize"
        assert res.valid is True

    def test_tokenize_phase_failure(self, runner_factory, mock_svc):
        r = runner_factory(smoke=True)

        with patch.object(r, "_warmup"):
            with patch.object(r, "_call_llm", side_effect=RuntimeError("boom")):
                r._run_tokenize_phase()

        assert len(r.results) == 1
        assert r.results[0].valid is False

    def test_multiple_attempts(self, runner_factory, mock_svc):
        r = runner_factory(attempts=3, evalset=["block_01"])

        with patch.object(r, "_call_llm") as mock_llm:
            mock_llm.return_value = "你好\n世界\n"
            r._run_translate_phase()

        assert len(r.results) == 3
        attempts = sorted(res.attempt for res in r.results)
        assert attempts == [1, 2, 3]

    def test_run_stops_service_on_failure(self, runner_factory, mock_svc):
        r = runner_factory(smoke=True)

        with patch.object(r, "_warmup"):
            with patch.object(r, "_call_llm", side_effect=RuntimeError("boom")):
                r.run()

        mock_svc.stop.assert_called_with("llm")

    def test_translate_refusal_marked_invalid(self, runner_factory, mock_svc):
        r = runner_factory(smoke=True)

        with patch.object(r, "_call_llm") as mock_llm:
            mock_llm.return_value = "Sorry, I can't translate this."
            r._run_translate_phase()

        assert r.results[0].valid is False

    def test_no_golden_translation_skips_tokenize(self, runner_factory, mock_svc, eval_fixture):
        (eval_fixture / "block_01_golden_translation.txt").unlink()
        r = runner_factory(smoke=True)
        mock_llm = MagicMock()

        with patch.object(r, "_call_llm", mock_llm):
            r._run_tokenize_phase()

        assert len(r.results) == 0


class TestBenchmarkRunnerReport:
    def test_print_report(self, runner_factory, capsys):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                validation=TranslateValidation(True, True, True, True, total_sentences=2),
            ),
            TaskResult(
                task="tokenize", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=200.0, response_chars=20, valid=True,
                validation=TokenizeValidation(True, True, True, errors=None),
            ),
        ]
        r.print_report()
        out = capsys.readouterr().out
        assert "BENCHMARK REPORT" in out
        assert "Translate (1 runs)" in out
        assert "Tokenize (1 runs)" in out
        assert "OK" in out

    def test_print_report_empty(self, runner_factory, capsys):
        r = runner_factory(smoke=True)
        r.print_report()
        out = capsys.readouterr().out
        assert "(no results)" in out


class TestBenchmarkRunnerToJson:
    def test_empty(self, runner_factory):
        r = runner_factory(smoke=True)
        d = r.to_json()
        assert d["config"]["attempts"] == 1
        assert d["config"]["evalset"] == ["block_01"]
        assert d["results"] == []

    def test_with_results(self, runner_factory):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                validation=TranslateValidation(True, True, True, True, total_sentences=2),
            ),
        ]
        d = r.to_json()
        assert len(d["results"]) == 1
        assert d["results"][0]["task"] == "translate"
        assert d["results"][0]["validation"]["total_sentences"] == 2

    def test_json_serializable(self, runner_factory):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                validation=TranslateValidation(True, True, True, True, total_sentences=2),
            ),
        ]
        d = r.to_json()
        json.dumps(d)  # must not raise

    def test_with_calibration(self, runner_factory):
        r = runner_factory(smoke=True)
        r.calibration = {"block_01": {"total_token_errors": 5, "token_error_rate": 0.01}}
        d = r.to_json()
        assert d["calibration"]["block_01"]["total_token_errors"] == 5


# ---------------------------------------------------------------------------
# Server metrics reconciliation
# ---------------------------------------------------------------------------

class TestPopulateServerMetrics:
    def test_populates_from_log_record(self):
        results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=500.0, response_chars=10,
                valid=True,
                validation=TranslateValidation(True, True, True, True, total_sentences=2),
            ),
            TaskResult(
                task="tokenize", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=800.0, response_chars=50,
                valid=True,
                validation=TokenizeValidation(True, True, True, errors=None),
            ),
        ]
        # Attach wall times
        results[0]._wall_start = datetime(2026, 7, 4, 21, 50, 0, tzinfo=timezone.utc)
        results[0]._wall_end = datetime(2026, 7, 4, 21, 50, 5, tzinfo=timezone.utc)
        results[1]._wall_start = datetime(2026, 7, 4, 21, 51, 0, tzinfo=timezone.utc)
        results[1]._wall_end = datetime(2026, 7, 4, 21, 51, 8, tzinfo=timezone.utc)

        log_records = [
            RequestRecord(
                log_format="legacy",
                start_time="2026-07-04T21:50:01",
                prompt_eval_time_ms=1200.0, prompt_eval_tokens=512,
                eval_time_ms=2800.0, eval_tokens=185,
                total_time_ms=4000.0, total_tokens=697,
                prompt_eval_tokens_per_sec=426.7, eval_tokens_per_sec=66.1,
            ),
            RequestRecord(
                log_format="legacy",
                start_time="2026-07-04T21:51:02",
                prompt_eval_time_ms=2300.0, prompt_eval_tokens=890,
                eval_time_ms=5500.0, eval_tokens=420,
                total_time_ms=7800.0, total_tokens=1310,
                prompt_eval_tokens_per_sec=387.0, eval_tokens_per_sec=76.4,
            ),
        ]

        _populate_server_metrics(results, log_records)

        assert results[0].prompt_eval_ms == 1200.0
        assert results[0].prompt_tokens == 512
        assert results[0].eval_ms == 2800.0
        assert results[0].eval_tokens == 185
        assert results[0].total_ms == 4000.0
        assert results[0].total_tokens == 697
        assert results[0].prompt_tps == 426.7
        assert results[0].eval_tps == 66.1

        assert results[1].prompt_eval_ms == 2300.0
        assert results[1].prompt_tokens == 890
        assert results[1].eval_tokens == 420

    def test_no_wall_time_leaves_fields_none(self):
        results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=500.0, response_chars=10,
                valid=True,
                validation=TranslateValidation(True, True, True, True, total_sentences=2),
            ),
        ]
        log_records = [
            RequestRecord(
                log_format="legacy",
                start_time="2026-07-04T21:50:01",
                prompt_eval_tokens=512,
            ),
        ]

        _populate_server_metrics(results, log_records)

        assert results[0].prompt_tokens is None
        assert results[0].eval_tokens is None

    def test_no_matching_log_record(self):
        results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=500.0, response_chars=10,
                valid=True,
                validation=TranslateValidation(True, True, True, True, total_sentences=2),
            ),
        ]
        results[0]._wall_start = datetime(2026, 7, 4, 21, 50, 0, tzinfo=timezone.utc)
        results[0]._wall_end = datetime(2026, 7, 4, 21, 50, 5, tzinfo=timezone.utc)

        # Log record outside the time window
        log_records = [
            RequestRecord(
                log_format="legacy",
                start_time="2026-07-04T23:00:01",
                prompt_eval_tokens=512,
            ),
        ]

        _populate_server_metrics(results, log_records)

        assert results[0].prompt_tokens is None


class TestFindLogRecord:
    def test_finds_by_time_proximity(self):
        wall_start = datetime(2026, 7, 4, 21, 50, 0, tzinfo=timezone.utc)
        wall_end = datetime(2026, 7, 4, 21, 50, 5, tzinfo=timezone.utc)

        result = TaskResult(
            task="translate", model="m", eval_case_id="block_01",
            attempt=1, wall_time_ms=500.0, response_chars=10,
            valid=True,
            validation=TranslateValidation(True, True, True, True, total_sentences=2),
        )
        result._wall_start = wall_start
        result._wall_end = wall_end

        target = RequestRecord(
            log_format="legacy",
            start_time="2026-07-04T21:50:02",
            prompt_eval_tokens=512,
        )

        records = [
            RequestRecord(log_format="legacy", start_time="2026-07-04T20:00:00"),
            target,
            RequestRecord(log_format="legacy", start_time="2026-07-04T22:00:00"),
        ]

        found = _find_log_record(result, records)
        assert found is target

    def test_returns_none_when_no_wall_times(self):
        result = TaskResult(
            task="translate", model="m", eval_case_id="block_01",
            attempt=1, wall_time_ms=500.0, response_chars=10,
            valid=True,
            validation=TranslateValidation(True, True, True, True, total_sentences=2),
        )
        records = [
            RequestRecord(log_format="legacy", start_time="2026-07-04T21:50:01"),
        ]

        found = _find_log_record(result, records)
        assert found is None

    def test_returns_none_when_no_match(self):
        wall_start = datetime(2026, 7, 4, 21, 50, 0, tzinfo=timezone.utc)
        wall_end = datetime(2026, 7, 4, 21, 50, 5, tzinfo=timezone.utc)

        result = TaskResult(
            task="translate", model="m", eval_case_id="block_01",
            attempt=1, wall_time_ms=500.0, response_chars=10,
            valid=True,
            validation=TranslateValidation(True, True, True, True, total_sentences=2),
        )
        result._wall_start = wall_start
        result._wall_end = wall_end

        records = [
            RequestRecord(log_format="legacy", start_time="2026-07-04T23:00:01"),
        ]

        found = _find_log_record(result, records)
        assert found is None


class TestReconcileLogs:
    def test_populates_all_results(self):
        wall_start = datetime(2026, 7, 4, 21, 50, 0, tzinfo=timezone.utc)
        wall_end = datetime(2026, 7, 4, 21, 51, 8, tzinfo=timezone.utc)

        results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=500.0, response_chars=10,
                valid=True,
                validation=TranslateValidation(True, True, True, True, total_sentences=2),
            ),
        ]
        results[0]._wall_start = datetime(2026, 7, 4, 21, 50, 1, tzinfo=timezone.utc)
        results[0]._wall_end = datetime(2026, 7, 4, 21, 50, 4, tzinfo=timezone.utc)

        log_record = RequestRecord(
            log_format="legacy",
            start_time="2026-07-04T21:50:02",
            prompt_eval_time_ms=1200.0, prompt_eval_tokens=512,
            eval_time_ms=2800.0, eval_tokens=185,
        )

        with patch("storyline.benchmark.runner.parse_window", return_value=[log_record]):
            reconcile_logs(results, wall_start, wall_end)

        assert results[0].prompt_tokens == 512
        assert results[0].eval_tokens == 185

    def test_graceful_on_parse_failure(self, caplog):
        results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=500.0, response_chars=10,
                valid=True,
                validation=TranslateValidation(True, True, True, True, total_sentences=2),
            ),
        ]

        with patch("storyline.benchmark.runner.parse_window", side_effect=RuntimeError("journalctl unavailable")):
            reconcile_logs(
                results,
                datetime(2026, 7, 4, 21, 50, tzinfo=timezone.utc),
                datetime(2026, 7, 4, 21, 51, tzinfo=timezone.utc),
            )

        assert results[0].prompt_tokens is None

    def test_no_log_records_leaves_fields_none(self):
        results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=500.0, response_chars=10,
                valid=True,
                validation=TranslateValidation(True, True, True, True, total_sentences=2),
            ),
        ]

        with patch("storyline.benchmark.runner.parse_window", return_value=[]):
            reconcile_logs(
                results,
                datetime(2026, 7, 4, 21, 50, tzinfo=timezone.utc),
                datetime(2026, 7, 4, 21, 51, tzinfo=timezone.utc),
            )

        assert results[0].prompt_tokens is None


# ---------------------------------------------------------------------------
# Wall-time tracking
# ---------------------------------------------------------------------------

class TestWallTimeTracking:
    def test_wall_times_set_on_results(self, runner_factory):
        r = runner_factory(smoke=True)

        with patch.object(r, "_call_llm") as mock_llm:
            mock_llm.return_value = "你好\n世界\n"
            r._run_translate_phase()

        assert len(r.results) == 1
        res = r.results[0]
        assert hasattr(res, "_wall_start")
        assert hasattr(res, "_wall_end")
        assert isinstance(res._wall_start, datetime)
        assert isinstance(res._wall_end, datetime)
        assert res._wall_end >= res._wall_start

    def test_wall_bounds_collected(self, runner_factory):
        r = runner_factory(smoke=True)

        with patch.object(r, "_call_llm") as mock_llm:
            mock_llm.return_value = "你好\n世界\n"
            r._run_translate_phase()

        # At least 2 bounds collected (start + end, possibly more if parsing logs)
        assert len(r._wall_bounds) >= 2 if r.parse_logs else len(r._wall_bounds) >= 0

    def test_wall_times_set_on_failure(self, runner_factory):
        r = runner_factory(smoke=True)

        with patch.object(r, "_warmup"):
            with patch.object(r, "_call_llm", side_effect=RuntimeError("boom")):
                r._run_translate_phase()

        assert len(r.results) == 1
        res = r.results[0]
        assert hasattr(res, "_wall_start")
        assert hasattr(res, "_wall_end")


# ---------------------------------------------------------------------------
# Report with server metrics
# ---------------------------------------------------------------------------

class TestReportWithServerMetrics:
    def test_print_report_includes_server_info(self, runner_factory, capsys):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                prompt_eval_ms=1200.0, prompt_tokens=512,
                eval_ms=2800.0, eval_tokens=185,
                total_ms=4000.0, total_tokens=697,
                validation=TranslateValidation(True, True, True, True, total_sentences=2),
            ),
        ]
        r.print_report()
        out = capsys.readouterr().out
        assert "prompt=512tok/1200ms" in out
        assert "eval=185tok/2800ms" in out

    def test_print_report_without_server_info(self, runner_factory, capsys):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                validation=TranslateValidation(True, True, True, True, total_sentences=2),
            ),
        ]
        r.print_report()
        out = capsys.readouterr().out
        # Should NOT include server info when not available
        assert "prompt=" not in out

    def test_print_report_includes_golden_metrics(self, runner_factory, capsys):
        from storyline.benchmark.token_compare import ComparisonResult, ComparisonMeta, GlobalMetrics

        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="tokenize", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=200.0, response_chars=50, valid=True,
                validation=TokenizeValidation(
                    True, True, True, errors=None,
                    golden=ComparisonResult(
                        meta=ComparisonMeta("a", "b", 2, 2, 4, 4),
                        global_metrics=GlobalMetrics(2, 0, 0, 2, 0, 2, 2, 2, 0.033),
                    ),
                ),
            ),
        ]
        r.print_report()
        out = capsys.readouterr().out
        assert "golden: 2 tok-errs rate=0.033" in out


class TestJsonOutputWithServerMetrics:
    def test_includes_server_metrics(self, runner_factory):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                prompt_eval_ms=1200.0, prompt_tokens=512,
                eval_ms=2800.0, eval_tokens=185,
                validation=TranslateValidation(True, True, True, True, total_sentences=2),
            ),
        ]
        d = r.to_json()
        res = d["results"][0]
        assert res["prompt_eval_ms"] == 1200.0
        assert res["prompt_tokens"] == 512
        assert res["eval_ms"] == 2800.0
        assert res["eval_tokens"] == 185

    def test_server_metrics_absent_when_none(self, runner_factory):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                validation=TranslateValidation(True, True, True, True, total_sentences=2),
            ),
        ]
        d = r.to_json()
        res = d["results"][0]
        assert res["prompt_eval_ms"] is None
        assert res["prompt_tokens"] is None
        assert res["valid"] is True

    def test_json_excludes_internal_wall_attrs(self, runner_factory):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                validation=TranslateValidation(True, True, True, True, total_sentences=2),
            ),
        ]
        r.results[0]._wall_start = datetime(2026, 7, 4, 21, 50, tzinfo=timezone.utc)
        r.results[0]._wall_end = datetime(2026, 7, 4, 21, 50, 5, tzinfo=timezone.utc)
        d = r.to_json()
        res = d["results"][0]
        assert "_wall_start" not in res
        assert "_wall_end" not in res


class TestBenchmarkRunnerServiceLifecycle:
    def test_warmup_called_once_in_run(self, runner_factory, mock_svc):
        r = runner_factory(smoke=True)
        with patch.object(r, "_call_llm") as mock_llm:
            mock_llm.return_value = "你好\n世界\n"
            r.run()

        # warmup should have been called exactly once (by run(), not phases)
        warmup_calls = [call for call in mock_llm.call_args_list
                        if "warmup" in str(call[0][0]).lower()]
        assert len(warmup_calls) == 1
        mock_svc.ensure_llm_profile.assert_called_with("local-llamacpp")

    def test_profile_switched_for_tokenize(self, runner_factory, mock_svc):
        r = runner_factory(
            translate_models=["model-a"],
            tokenize_models=["model-b"],
            translate_profile="model-a",
            tokenize_profile="model-b",
            smoke=True,
        )

        with patch.object(r, "_call_llm") as mock_llm:
            mock_llm.return_value = "你好\n世界\n"
            r.run()

        # ensure_llm_profile called with model-a (once for startup + warmup)
        # and model-b (when switching for tokenize)
        profile_calls = [c[0][0] for c in mock_svc.ensure_llm_profile.call_args_list]
        assert "model-a" in profile_calls
        assert "model-b" in profile_calls

    def test_run_always_stops_service(self, runner_factory, mock_svc):
        r = runner_factory(smoke=True)
        with patch.object(r, "_call_llm") as mock_llm:
            mock_llm.return_value = "你好\n世界\n"
            r.run()

        mock_svc.stop.assert_called_with("llm")


# ---------------------------------------------------------------------------
# Translate check phase
# ---------------------------------------------------------------------------

class TestTranslateCheckPhase:
    def test_populates_check_results(self, runner_factory):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                _response_raw="你好\n世界\n",
                validation=TranslateValidation(
                    True, True, True, True, total_sentences=2,
                ),
            ),
        ]

        with patch("storyline.benchmark.runner.call_translation_check") as mock_check:
            mock_check.return_value = "1:grammar:bad grammar\n"
            r._run_translate_check_phase()

        v = r.results[0].validation
        assert isinstance(v, TranslateValidation)
        assert v.check_errors == ["1:grammar:bad grammar"]
        assert v.error_ratio == 0.5

    def test_no_valid_results_skips_check(self, runner_factory):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=0, valid=False,
                _response_raw="",
                validation=TranslateValidation(
                    False, False, False, False,
                ),
            ),
        ]

        with patch("storyline.benchmark.runner.call_translation_check") as mock_check:
            r._run_translate_check_phase()

        mock_check.assert_not_called()

    def test_empty_aggregated_text_skips_check(self, runner_factory):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=0, valid=True,
                _response_raw="",
                validation=TranslateValidation(
                    True, True, True, True, total_sentences=0,
                ),
            ),
        ]

        with patch("storyline.benchmark.runner.call_translation_check") as mock_check:
            r._run_translate_check_phase()

        mock_check.assert_not_called()

    def test_check_call_failure_graceful(self, runner_factory):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                _response_raw="你好\n世界\n",
                validation=TranslateValidation(
                    True, True, True, True, total_sentences=2,
                ),
            ),
        ]

        with patch("storyline.benchmark.runner.call_translation_check",
                   side_effect=RuntimeError("API error")):
            r._run_translate_check_phase()

        # Should not raise; check_results remain None
        v = r.results[0].validation
        assert isinstance(v, TranslateValidation)
        assert v.check_errors is None

    def test_partitions_errors_across_blocks(self, runner_factory):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                _response_raw="S1\nS2\n",
                validation=TranslateValidation(
                    True, True, True, True, total_sentences=2,
                ),
            ),
            TaskResult(
                task="translate", model="m", eval_case_id="block_02",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                _response_raw="S3\nS4\n",
                validation=TranslateValidation(
                    True, True, True, True, total_sentences=2,
                ),
            ),
        ]

        with patch("storyline.benchmark.runner.call_translation_check") as mock_check:
            mock_check.return_value = "1:grammar:err1\n4:style:err2\n"
            r._run_translate_check_phase()

        v1 = r.results[0].validation
        v2 = r.results[1].validation
        assert isinstance(v1, TranslateValidation)
        assert isinstance(v2, TranslateValidation)
        assert v1.check_errors == ["1:grammar:err1"]
        assert v2.check_errors == ["4:style:err2"]
        assert v1.error_ratio == 0.5
        assert v2.error_ratio == 0.5

    def test_only_valid_translate_results_checked(self, runner_factory):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                _response_raw="S1\n",
                validation=TranslateValidation(
                    True, True, True, True, total_sentences=1,
                ),
            ),
            TaskResult(
                task="translate", model="m", eval_case_id="block_02",
                attempt=1, wall_time_ms=100.0, response_chars=0, valid=False,
                _response_raw="",
                validation=TranslateValidation(
                    False, False, False, False,
                ),
            ),
        ]

        with patch("storyline.benchmark.runner.call_translation_check") as mock_check:
            mock_check.return_value = "1:grammar:err\n"
            r._run_translate_check_phase()

        v1 = r.results[0].validation
        v2 = r.results[1].validation
        assert isinstance(v1, TranslateValidation)
        assert isinstance(v2, TranslateValidation)
        assert v1.check_errors == ["1:grammar:err"]
        assert v2.check_errors is None


class TestReportWithTranslateCheck:
    def test_print_report_includes_check_errors(self, runner_factory, capsys):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                validation=TranslateValidation(
                    True, True, True, True, total_sentences=2,
                    check_errors=["1:grammar:bad"],
                    error_ratio=0.5,
                ),
            ),
        ]
        r.print_report()
        out = capsys.readouterr().out
        assert "check_errs=1" in out
        assert "ratio=0.500" in out

    def test_print_report_without_check_errors(self, runner_factory, capsys):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                validation=TranslateValidation(
                    True, True, True, True, total_sentences=2,
                ),
            ),
        ]
        r.print_report()
        out = capsys.readouterr().out
        assert "check_errs" not in out


class TestJsonOutputWithTranslateCheck:
    def test_includes_check_errors_in_json(self, runner_factory):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                validation=TranslateValidation(
                    True, True, True, True, total_sentences=2,
                    check_errors=["1:grammar:bad", "2:style:weird"],
                    error_ratio=1.0,
                ),
            ),
        ]
        d = r.to_json()
        v = d["results"][0]["validation"]
        assert v["check_errors"] == ["1:grammar:bad", "2:style:weird"]
        assert v["error_ratio"] == 1.0

    def test_null_check_errors_in_json(self, runner_factory):
        r = runner_factory(smoke=True)
        r.results = [
            TaskResult(
                task="translate", model="m", eval_case_id="block_01",
                attempt=1, wall_time_ms=100.0, response_chars=10, valid=True,
                validation=TranslateValidation(
                    True, True, True, True, total_sentences=2,
                ),
            ),
        ]
        d = r.to_json()
        v = d["results"][0]["validation"]
        assert v["check_errors"] is None
        assert v["error_ratio"] is None


class TestConfigLoadingWithCheck:
    def test_loads_check_models(self):
        with patch("storyline.benchmark.runner.Path.open", side_effect=FileNotFoundError):
            t, tok, check, _, _, repeat = _load_config()
            assert t == ["local-llamacpp"]
            assert tok == ["local-llamacpp"]
            assert check == ["glm-4.7-flash"]
            assert repeat == 1