"""Tests for the monthly-spend forecaster."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from milo_usage_forecaster import forecast_engine, log_parser


def test_forecast_with_empty_log_returns_zero_spend(tmp_path: Path) -> None:
    out = forecast_engine.forecast_from_logs(log_path=str(tmp_path))
    assert out.spend_so_far_usd == 0.0
    assert out.projected_end_of_month_usd == 0.0
    assert out.sample_event_count == 0
    assert "No usage events" in out.note


def test_forecast_from_synthetic_logs_returns_positive_spend(synthetic_log_root_set: Path) -> None:
    out = forecast_engine.forecast_from_logs()
    assert out.sample_event_count > 0
    assert out.projected_end_of_month_usd > 0.0
    # Confidence band is sane: lo <= mid <= hi
    assert out.confidence_lo_usd <= out.confidence_mid_usd <= out.confidence_hi_usd
    # Days-elapsed + days-remaining add up to days_in_month
    assert out.days_elapsed + out.days_remaining == out.days_in_month
    # Month label is YYYY-MM
    assert len(out.month_label) == 7 and out.month_label[4] == "-"


def test_forecast_explicit_log_path(synthetic_log_dir: Path, tmp_path: Path, monkeypatch) -> None:
    """Passing log_path explicitly overrides the default env-driven root."""
    # Move default root away to confirm explicit log_path wins
    monkeypatch.setenv("MILO_USAGE_FORECASTER_LOG_ROOT", str(tmp_path / "empty"))
    out = forecast_engine.forecast_from_logs(log_path=str(synthetic_log_dir))
    assert out.sample_event_count > 0


def test_forecast_current_month_days_override(synthetic_log_root_set: Path) -> None:
    """Caller can override days-elapsed (e.g. for backfill scenarios)."""
    out = forecast_engine.forecast_from_logs(current_month_days_so_far=5)
    assert out.days_elapsed == 5
    assert out.days_remaining == out.days_in_month - 5


def test_forecast_low_sample_warning(tmp_path: Path) -> None:
    """When fewer than 20 events, the note explicitly flags shaky data."""
    p = tmp_path / "tiny.jsonl"
    # 3 events, all in the last 30 days
    from datetime import datetime, timedelta, timezone
    import json
    rows = []
    for d in (5, 4, 3):
        ts = (datetime.now(tz=timezone.utc) - timedelta(days=d)).isoformat().replace("+00:00", "Z")
        rows.append({
            "type": "assistant", "timestamp": ts,
            "message": {
                "model": "claude-opus-4-7",
                "usage": {"input_tokens": 100, "output_tokens": 200,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            },
        })
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = forecast_engine.forecast_from_logs(log_path=str(p))
    assert out.sample_event_count == 3
    assert "shaky" in out.note.lower()


def test_forecast_confidence_band_widens_with_variance() -> None:
    """A choppy spend curve should produce a wider band than a smooth one."""
    from datetime import datetime, timezone, timedelta
    from milo_usage_forecaster.log_parser import UsageEvent
    now = datetime.now(tz=timezone.utc)
    # Smooth: identical $X/day for 30 days
    smooth_events = [
        UsageEvent(
            timestamp=(now - timedelta(days=d)).isoformat().replace("+00:00", "Z"),
            model="claude-opus-4-7",
            project="p", file="f",
            input_tokens=1000, output_tokens=1000,
        ) for d in range(1, 31)
    ]
    # Choppy: alternating tiny + huge
    choppy_events = [
        UsageEvent(
            timestamp=(now - timedelta(days=d)).isoformat().replace("+00:00", "Z"),
            model="claude-opus-4-7",
            project="p", file="f",
            input_tokens=10 if d % 2 else 100_000,
            output_tokens=10 if d % 2 else 100_000,
        ) for d in range(1, 31)
    ]
    smooth = forecast_engine.forecast(smooth_events)
    choppy = forecast_engine.forecast(choppy_events)
    smooth_band = smooth.confidence_hi_usd - smooth.confidence_lo_usd
    choppy_band = choppy.confidence_hi_usd - choppy.confidence_lo_usd
    assert choppy_band > smooth_band


def test_forecast_handles_missing_log_root_gracefully(monkeypatch, tmp_path: Path) -> None:
    """Default root doesn't exist -> empty forecast, no crash."""
    nonexistent = tmp_path / "does-not-exist"
    monkeypatch.setenv("MILO_USAGE_FORECASTER_LOG_ROOT", str(nonexistent))
    out = forecast_engine.forecast_from_logs()
    assert out.sample_event_count == 0


def test_method_id_is_set(synthetic_log_root_set: Path) -> None:
    out = forecast_engine.forecast_from_logs()
    assert out.method == "rolling-7d-avg+dow-seasonality"
