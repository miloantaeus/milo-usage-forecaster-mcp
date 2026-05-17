"""Tests for the local log parser (Claude Code JSONL + Cursor JSON)."""

from __future__ import annotations

from pathlib import Path

import pytest

from milo_usage_forecaster import log_parser


def test_parse_static_claude_code_jsonl(static_claude_code_log_path: Path) -> None:
    """The static fixture has 11 assistant rows + non-assistant noise; only 11 should parse."""
    events = log_parser.collect_events(log_path=str(static_claude_code_log_path))
    assert len(events) == 11
    # All events have a populated model + non-zero tokens
    for ev in events:
        assert ev.model
        assert ev.input_tokens + ev.output_tokens > 0
        assert ev.timestamp.endswith("Z")


def test_parse_static_cursor_json(static_cursor_log_path: Path) -> None:
    """The static cursor fixture has 3 records."""
    events = log_parser.collect_events(log_path=str(static_cursor_log_path))
    assert len(events) == 3
    # All cursor events have model + subagent set
    models = {ev.model for ev in events}
    assert "gpt-5.5" in models or "claude-sonnet-4.6" in models


def test_parse_skips_non_assistant_rows(static_claude_code_log_path: Path) -> None:
    """user / queue-operation rows should be filtered out, not crash."""
    events = log_parser.collect_events(log_path=str(static_claude_code_log_path))
    # No events should have an empty model field
    for ev in events:
        assert ev.model and ev.model != "unknown"


def test_subagent_extracted_from_scheduled_task_tag(static_claude_code_log_path: Path) -> None:
    """When message content starts with <scheduled-task name="X">, subagent should be X."""
    events = log_parser.collect_events(log_path=str(static_claude_code_log_path))
    subagents = {ev.subagent for ev in events}
    # The fixture has one row with a scheduled-task tag
    assert "milo-hourly-supervision" in subagents


def test_event_to_cost_usd_known_model() -> None:
    """Cost calc for a known model uses fresh + cache + output rates."""
    from milo_usage_forecaster.log_parser import UsageEvent
    ev = UsageEvent(
        timestamp="2026-05-16T10:00:00Z",
        model="claude-opus-4-7",
        project="p", file="f",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    cost = log_parser.event_to_cost_usd(ev)
    # opus-4.7: $5 in + $25 out = $30 for 1M+1M
    assert cost == pytest.approx(30.0, rel=1e-3)


def test_event_to_cost_usd_unknown_model_returns_zero() -> None:
    from milo_usage_forecaster.log_parser import UsageEvent
    ev = UsageEvent(
        timestamp="2026-05-16T10:00:00Z",
        model="mystery-llm-7b",
        project="p", file="f",
        input_tokens=1000, output_tokens=500,
    )
    assert log_parser.event_to_cost_usd(ev) == 0.0


def test_pricing_table_handles_dash_suffix() -> None:
    """Claude Code logs use 'claude-opus-4-7'; pricing table has 'claude-opus-4.7'."""
    from milo_usage_forecaster.pricing_table import lookup
    p = lookup("claude-opus-4-7")
    assert p is not None
    assert p.model == "claude-opus-4.7"


def test_iter_events_skips_unreadable_files(tmp_path: Path) -> None:
    """Permission errors / decode errors should not crash the parse."""
    p = tmp_path / "good.jsonl"
    p.write_text(
        '{"type":"assistant","timestamp":"2026-05-16T10:00:00Z",'
        '"message":{"model":"claude-opus-4-7",'
        '"usage":{"input_tokens":100,"output_tokens":200,'
        '"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}',
        encoding="utf-8",
    )
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(b"\x00\x01garbage\x02not-json")
    events = log_parser.collect_events(log_path=str(tmp_path))
    # Good file parses, bad file silently skipped
    assert len(events) == 1


def test_events_in_window_filters_by_age() -> None:
    """events_in_window respects the days cutoff."""
    from datetime import datetime, timedelta, timezone
    from milo_usage_forecaster.log_parser import UsageEvent
    now = datetime.now(tz=timezone.utc)
    in_window = UsageEvent(
        timestamp=(now - timedelta(days=2)).isoformat().replace("+00:00", "Z"),
        model="claude-opus-4-7", project="p", file="f",
        input_tokens=10, output_tokens=10,
    )
    out_of_window = UsageEvent(
        timestamp=(now - timedelta(days=60)).isoformat().replace("+00:00", "Z"),
        model="claude-opus-4-7", project="p", file="f",
        input_tokens=10, output_tokens=10,
    )
    filtered = log_parser.events_in_window([in_window, out_of_window], days=7)
    assert len(filtered) == 1
    assert filtered[0] is in_window


def test_collect_events_default_root_when_unset(monkeypatch, tmp_path: Path) -> None:
    """Without a log_path arg, falls back to MILO_USAGE_FORECASTER_LOG_ROOT."""
    monkeypatch.setenv("MILO_USAGE_FORECASTER_LOG_ROOT", str(tmp_path))
    events = log_parser.collect_events()
    assert events == []  # empty dir, no crash
