"""Tests for the budget-alert checker."""

from __future__ import annotations

from pathlib import Path

import pytest

from milo_usage_forecaster import budget_alert, telemetry


def test_budget_check_clear_when_cap_far_above_projection(synthetic_log_root_set: Path) -> None:
    """A huge cap relative to spend → level 'clear', positive headroom."""
    out = budget_alert.check_budget(monthly_cap_usd=100_000.0)
    assert out.level == "clear"
    assert out.headroom_usd > 0
    assert out.breach_risk_pct <= 30.0


def test_budget_check_breached_when_cap_below_spend(synthetic_log_root_set: Path) -> None:
    """A tiny cap → level 'breached', headroom negative."""
    out = budget_alert.check_budget(monthly_cap_usd=0.01)
    assert out.level == "breached"
    assert out.headroom_usd < 0


def test_budget_check_returns_hours_until_breach_when_at_risk(synthetic_log_root_set: Path) -> None:
    """A cap close to projection should return a numeric hours_until_breach."""
    # First call to figure out the projection, then pick a cap slightly below it.
    discovery = budget_alert.check_budget(monthly_cap_usd=999_999.0, enforce_rate_limit=False)
    cap = discovery.projected_end_of_month_usd * 0.5
    if cap <= 0:
        pytest.skip("Synthetic data produced zero projection — can't test breach hours")
    out = budget_alert.check_budget(monthly_cap_usd=cap, enforce_rate_limit=False)
    # Either breached or has a positive breach window
    assert out.level in {"warn", "urgent", "breached"}
    if out.level != "breached":
        assert out.hours_until_breach is not None
        assert out.hours_until_breach >= 0


def test_budget_check_rejects_zero_cap() -> None:
    out = budget_alert.check_budget(monthly_cap_usd=0.0)
    assert "monthly_cap_usd must be > 0" in out.note
    assert out.level == "clear"


def test_budget_check_rate_limit_kicks_in(synthetic_log_root_set: Path) -> None:
    """Free tier is capped at 3 calls/day; 4th call returns rate_limited."""
    for _ in range(budget_alert.FREE_DAILY_CAP):
        out = budget_alert.check_budget(monthly_cap_usd=100.0)
        assert out.rate_limited is False
    # Next call should be rate-limited.
    capped = budget_alert.check_budget(monthly_cap_usd=100.0)
    assert capped.rate_limited is True
    assert "cap" in capped.note.lower()
    assert capped.rate_limit_remaining_today == 0


def test_budget_check_rate_limit_can_be_bypassed_for_tests() -> None:
    """enforce_rate_limit=False lets tests run as many checks as needed."""
    for _ in range(budget_alert.FREE_DAILY_CAP + 5):
        out = budget_alert.check_budget(
            monthly_cap_usd=100.0, enforce_rate_limit=False,
        )
        assert out.rate_limited is False


def test_budget_check_does_not_double_count_rate_limited_calls() -> None:
    """Once rate-limited, further calls don't keep advancing the counter."""
    for _ in range(budget_alert.FREE_DAILY_CAP):
        budget_alert.check_budget(monthly_cap_usd=100.0)
    pre_count = telemetry.get_daily_cap_count(budget_alert.TOOL_NAME)
    # Trigger a rate-limited reply
    out = budget_alert.check_budget(monthly_cap_usd=100.0)
    assert out.rate_limited is True
    post_count = telemetry.get_daily_cap_count(budget_alert.TOOL_NAME)
    assert pre_count == post_count, (
        "Rate-limited calls should not advance the per-day cap counter"
    )


def test_budget_check_level_progression() -> None:
    """Level mapping is deterministic given risk + headroom."""
    assert budget_alert._level_from_risk(95.0, headroom=10.0) == "urgent"
    assert budget_alert._level_from_risk(50.0, headroom=10.0) == "warn"
    assert budget_alert._level_from_risk(10.0, headroom=10.0) == "clear"
    assert budget_alert._level_from_risk(10.0, headroom=-5.0) == "breached"
