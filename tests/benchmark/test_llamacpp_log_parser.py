"""Tests for storyline.benchmark.llamacpp_log_parser (new functions)."""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from storyline.benchmark.llamacpp_log_parser import (
    RequestRecord,
    fetch_logs,
    parse_logs,
    parse_window,
    find_request_in_window,
    _parse_iso_ts,
    _parse_record_start,
    _record_in_window,
)


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

class TestParseISOTs:
    def test_with_offset(self):
        ts = _parse_iso_ts("2026-07-04T21:56:00-0700")
        assert ts.year == 2026
        assert ts.hour == 21
        assert ts.utcoffset() == timedelta(hours=-7)

    def test_with_colon_offset(self):
        ts = _parse_iso_ts("2026-07-04T21:56:00-07:00")
        assert ts.utcoffset() == timedelta(hours=-7)

    def test_without_offset(self):
        ts = _parse_iso_ts("2026-07-04T21:56:00")
        assert ts.year == 2026
        assert ts.utcoffset() == timedelta(0)  # UTC fallback

    def test_invalid(self):
        with pytest.raises(ValueError):
            _parse_iso_ts("not a timestamp")


# ---------------------------------------------------------------------------
# Record window checking
# ---------------------------------------------------------------------------

def _make_record(start_time: str) -> RequestRecord:
    return RequestRecord(start_time=start_time, log_format="legacy")


class TestRecordInWindow:
    def test_within(self):
        start = datetime(2026, 7, 4, 21, 50, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, 22, 10, tzinfo=timezone.utc)
        rec = _make_record("2026-07-04T21:56:00")
        assert _record_in_window(rec, start, end) is True

    def test_before(self):
        start = datetime(2026, 7, 4, 22, 0, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, 22, 10, tzinfo=timezone.utc)
        rec = _make_record("2026-07-04T21:56:00")
        assert _record_in_window(rec, start, end) is False

    def test_after(self):
        start = datetime(2026, 7, 4, 21, 0, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, 21, 50, tzinfo=timezone.utc)
        rec = _make_record("2026-07-04T21:56:00")
        assert _record_in_window(rec, start, end) is False

    def test_no_start_time(self):
        rec = RequestRecord(start_time=None)
        assert _record_in_window(rec, datetime.min.replace(tzinfo=timezone.utc),
                                 datetime.max.replace(tzinfo=timezone.utc)) is False

    def test_boundary_exclusive(self):
        start = datetime(2026, 7, 4, 21, 56, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, 22, 0, tzinfo=timezone.utc)
        rec = _make_record("2026-07-04T21:56:00")
        # start <= rec < end — start boundary inclusive, end exclusive
        assert _record_in_window(rec, start, end) is True

    def test_boundary_exclusive_end(self):
        start = datetime(2026, 7, 4, 21, 50, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, 21, 56, tzinfo=timezone.utc)
        rec = _make_record("2026-07-04T21:56:00")
        assert _record_in_window(rec, start, end) is False


# ---------------------------------------------------------------------------
# find_request_in_window
# ---------------------------------------------------------------------------

class TestFindRequestInWindow:
    def test_exact_match(self):
        start = datetime(2026, 7, 4, 21, 50, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, 22, 10, tzinfo=timezone.utc)
        records = [
            _make_record("2026-07-04T21:56:00"),
            _make_record("2026-07-04T22:00:00"),
        ]
        result = find_request_in_window(records, start, end)
        assert result is not None
        assert result.start_time == "2026-07-04T21:56:00"  # closest to start

    def test_closest_to_window_start(self):
        window_start = datetime(2026, 7, 4, 21, 59, tzinfo=timezone.utc)
        window_end = datetime(2026, 7, 4, 22, 30, tzinfo=timezone.utc)
        records = [
            _make_record("2026-07-04T21:50:00"),
            _make_record("2026-07-04T22:00:00"),
            _make_record("2026-07-04T22:05:00"),
        ]
        result = find_request_in_window(records, window_start, window_end)
        assert result is not None
        assert result.start_time == "2026-07-04T22:00:00"

    def test_no_match_returns_none(self):
        """When no ISO timestamps match the window, return None."""
        start = datetime(2026, 7, 4, 23, 0, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, 23, 10, tzinfo=timezone.utc)
        records = [
            _make_record("2026-07-04T21:56:00"),
        ]
        result = find_request_in_window(records, start, end)
        assert result is None

    def test_empty_records(self):
        result = find_request_in_window([], datetime.min.replace(tzinfo=timezone.utc),
                                        datetime.max.replace(tzinfo=timezone.utc))
        assert result is None


# ---------------------------------------------------------------------------
# parse_window
# ---------------------------------------------------------------------------

class TestParseWindow:
    def test_returns_all_parsed_records(self):
        """parse_window trusts journalctl time filtering; all parsed records are returned."""
        log_text = (
            "2026-07-04T21:50:00-0700 host python[1]: "
            "Llama.generate: Context reset requested or no prefix match. Cleared KV cache.\n"
            "2026-07-04T21:50:01-0700 host python[1]: llama_perf_context_print: "
            "prompt eval time = 100.00 ms / 50 tokens (2.00 ms per token, 500.00 tokens per second)\n"
            "2026-07-04T21:50:02-0700 host python[1]: llama_perf_context_print: "
            "eval time = 200.00 ms / 30 runs (6.67 ms per token, 150.00 tokens per second)\n"
            "2026-07-04T21:50:03-0700 host python[1]: llama_perf_context_print: "
            "total time = 300.00 ms / 80 tokens\n"
            '2026-07-04T21:50:04-0700 host python[1]: INFO: 127.0.0.1 - "POST /v1/chat/completions HTTP/1.1" 200\n'
            # Second request
            "2026-07-04T23:00:00-0700 host python[1]: "
            "Llama.generate: Context reset requested or no prefix match. Cleared KV cache.\n"
            "2026-07-04T23:00:01-0700 host python[1]: llama_perf_context_print: "
            "prompt eval time = 50.00 ms / 20 tokens (2.50 ms per token, 400.00 tokens per second)\n"
            "2026-07-04T23:00:02-0700 host python[1]: llama_perf_context_print: "
            "eval time = 100.00 ms / 15 runs (6.67 ms per token, 150.00 tokens per second)\n"
            '2026-07-04T23:00:03-0700 host python[1]: INFO: 127.0.0.1 - "POST /v1/chat/completions HTTP/1.1" 200\n'
        )

        with patch("storyline.benchmark.llamacpp_log_parser.fetch_logs", return_value=log_text):
            records = parse_window(
                start=datetime(2026, 7, 4, 21, 0, tzinfo=timezone.utc),
                end=datetime(2026, 7, 4, 22, 0, tzinfo=timezone.utc),
            )

        # journalctl does the time filtering; parse_window returns all parsed records
        assert len(records) == 2
        assert records[0].prompt_eval_tokens == 50
        assert records[1].prompt_eval_tokens == 20

    def test_empty_window(self):
        with patch("storyline.benchmark.llamacpp_log_parser.fetch_logs", return_value=""):
            records = parse_window(
                start=datetime(2026, 7, 4, 21, 0, tzinfo=timezone.utc),
                end=datetime(2026, 7, 4, 22, 0, tzinfo=timezone.utc),
            )
        assert records == []


# ---------------------------------------------------------------------------
# _parse_record_start
# ---------------------------------------------------------------------------

class TestParseRecordStart:
    def test_short_iso(self):
        rec = _make_record("2026-07-04T21:56:00-0700")
        ts = _parse_record_start(rec)
        assert ts is not None
        assert ts.year == 2026

    def test_none_start_time(self):
        assert _parse_record_start(RequestRecord()) is None

    def test_no_match(self):
        rec = _make_record("Jul 04 21:56:00")  # short format, no ISO prefix
        assert _parse_record_start(rec) is None