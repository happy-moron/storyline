"""Tests for storyline.benchmark.llamacpp_log_parser."""

from unittest.mock import patch

from storyline.benchmark.llamacpp_log_parser import (
    RequestRecord,
    fetch_logs,
    parse_logs,
    parse_window,
)


class TestParseNative:
    def test_parses_single_request(self):
        log_text = (
            "0.00.170.225 I srv  load_model: loading model 'models/qwen3-4b.gguf'\n"
            "1.01.276.446 I slot launch_slot_: id  3 | task 0 | \n"
            "1.01.276.500 I slot print_timing: id  3 | task 0 | "
            "prompt eval time = 100.00 ms / 50 tokens (2.00 ms per token, 500.00 tokens per second)\n"
            "1.01.276.600 I slot print_timing: id  3 | task 0 | "
            "eval time = 200.00 ms / 30 tokens (6.67 ms per token, 150.00 tokens per second)\n"
            "1.01.276.700 I slot print_timing: id  3 | task 0 | "
            "total time = 300.00 ms / 80 tokens\n"
            "1.01.276.800 I slot      release: id  3 | task 0 | "
            "stop processing: n_tokens = 85, truncated = 0\n"
        )

        records = parse_logs(log_text)
        assert len(records) == 1
        r = records[0]
        assert r.log_format == "native"
        assert r.model_hint == "qwen3-4b.gguf"
        assert r.slot_id == 3
        assert r.task_id == 0
        assert r.prompt_eval_tokens == 50
        assert r.prompt_eval_time_ms == 100.0
        assert r.eval_tokens == 30
        assert r.eval_time_ms == 200.0
        assert r.total_time_ms == 300.0
        assert r.total_tokens == 80
        assert r.n_tokens_final == 85
        assert r.truncated is False

    def test_multiple_requests(self):
        log_text = (
            "0.00.170.225 I srv  load_model: loading model 'models/m.gguf'\n"
            "1.01.276.446 I slot launch_slot_: id  1 | task 1 | \n"
            "1.01.276.500 I slot print_timing: id  1 | task 1 | "
            "prompt eval time = 50.00 ms / 20 tokens (2.50 ms per token, 400.00 tokens per second)\n"
            "1.01.276.600 I slot      release: id  1 | task 1 | "
            "stop processing: n_tokens = 25, truncated = 0\n"
            "2.02.277.000 I slot launch_slot_: id  2 | task 2 | \n"
            "2.02.277.100 I slot print_timing: id  2 | task 2 | "
            "prompt eval time = 60.00 ms / 30 tokens (2.00 ms per token, 500.00 tokens per second)\n"
            "2.02.277.200 I slot      release: id  2 | task 2 | "
            "stop processing: n_tokens = 35, truncated = 0\n"
        )

        records = parse_logs(log_text)
        assert len(records) == 2
        assert records[0].task_id == 1
        assert records[0].prompt_eval_tokens == 20
        assert records[1].task_id == 2
        assert records[1].prompt_eval_tokens == 30

    def test_empty_input(self):
        assert parse_logs("") == []

    def test_no_native_lines(self):
        assert parse_logs("this is just some random text\nno format here\n") == []

    def test_graceful_missing_release(self):
        # Request without a release line — not appended but flushed at end.
        # Empty print_timing lines means no meaningful data, so nothing is flushed.
        log_text = (
            "1.01.276.446 I slot launch_slot_: id  3 | task 0 | \n"
            "1.01.276.500 I slot print_timing: id  3 | task 0 | "
            "prompt eval time = 100.00 ms / 50 tokens (2.00 ms per token, 500.00 tokens per second)\n"
        )
        records = parse_logs(log_text)
        assert len(records) == 1
        assert records[0].prompt_eval_tokens == 50

    def test_load_time_parsed(self):
        log_text = (
            "1.01.276.446 I slot launch_slot_: id  3 | task 0 | \n"
            "0.50.123.456 I srv  load_model: load time = 2500.00 ms\n"
            "1.01.276.500 I slot print_timing: id  3 | task 0 | "
            "prompt eval time = 100.00 ms / 50 tokens (2.00 ms per token, 500.00 tokens per second)\n"
            "1.01.276.600 I slot      release: id  3 | task 0 | "
            "stop processing: n_tokens = 50, truncated = 0\n"
        )
        records = parse_logs(log_text)
        assert len(records) == 1
        assert records[0].prompt_eval_tokens == 50

    def test_cache_lcp_selection(self):
        log_text = (
            "1.01.276.000 I slot launch_slot_: id  3 | task -1 | "
            "selected slot by LCP similarity, sim_best = 0.95 (> 0.3 thold), f_keep = 0.8\n"
            "1.01.276.100 I srv  update_slots: prompt cache update took 50.0 ms\n"
            "1.01.276.446 I slot launch_slot_: id  3 | task 0 | \n"
            "1.01.276.500 I slot print_timing: id  3 | task 0 | "
            "prompt eval time = 100.00 ms / 50 tokens\n"
            "1.01.276.600 I slot      release: id  3 | task 0 | "
            "stop processing: n_tokens = 50, truncated = 0\n"
        )
        records = parse_logs(log_text)
        assert len(records) == 1
        r = records[0]
        assert r.cache_selected_by == "LCP_similarity"
        assert r.cache_similarity == 0.95
        assert r.cache_f_keep == 0.8
        assert r.cache_update_ms == 50.0

    def test_cache_lru_selection(self):
        log_text = (
            "1.01.276.000 I slot launch_slot_: id  3 | task -1 | "
            "selected slot by LRU, t_last = 1234\n"
            "1.01.276.446 I slot launch_slot_: id  3 | task 0 | \n"
            "1.01.276.500 I slot print_timing: id  3 | task 0 | "
            "prompt eval time = 100.00 ms / 50 tokens\n"
            "1.01.276.600 I slot      release: id  3 | task 0 | "
            "stop processing: n_tokens = 50, truncated = 0\n"
        )
        records = parse_logs(log_text)
        assert len(records) == 1
        r = records[0]
        assert r.cache_selected_by == "LRU"


class TestFetchLogs:
    def test_returns_raw_text(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "raw log output\n"
            result = fetch_logs(since="1 hour ago")
            assert result == "raw log output\n"


class TestParseWindow:
    def test_returns_all_parsed_records(self):
        log_text = (
            "0.00.170.225 I srv  load_model: loading model 'models/m.gguf'\n"
            "1.01.276.446 I slot launch_slot_: id  1 | task 1 | \n"
            "1.01.276.500 I slot print_timing: id  1 | task 1 | "
            "prompt eval time = 100.00 ms / 50 tokens (2.00 ms per token, 500.00 tokens per second)\n"
            "1.01.276.600 I slot      release: id  1 | task 1 | "
            "stop processing: n_tokens = 50, truncated = 0\n"
            "2.02.277.000 I slot launch_slot_: id  2 | task 2 | \n"
            "2.02.277.100 I slot print_timing: id  2 | task 2 | "
            "prompt eval time = 50.00 ms / 20 tokens (2.50 ms per token, 400.00 tokens per second)\n"
            "2.02.277.200 I slot      release: id  2 | task 2 | "
            "stop processing: n_tokens = 20, truncated = 0\n"
        )

        with patch("storyline.benchmark.llamacpp_log_parser.fetch_logs", return_value=log_text):
            from datetime import datetime, timezone
            records = parse_window(
                start=datetime(2026, 7, 4, 21, 0, tzinfo=timezone.utc),
                end=datetime(2026, 7, 4, 22, 0, tzinfo=timezone.utc),
            )

        assert len(records) == 2
        assert records[0].prompt_eval_tokens == 50
        assert records[1].prompt_eval_tokens == 20

    def test_empty_window(self):
        with patch("storyline.benchmark.llamacpp_log_parser.fetch_logs", return_value=""):
            from datetime import datetime, timezone
            records = parse_window(
                start=datetime(2026, 7, 4, 21, 0, tzinfo=timezone.utc),
                end=datetime(2026, 7, 4, 22, 0, tzinfo=timezone.utc),
            )
        assert records == []
