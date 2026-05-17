"""
Budget alert checker.

Given a monthly USD cap and the current spend curve, compute:
  - hours_until_breach: how long at the current rolling rate before you cross the cap
  - breach_risk_pct:    probability-of-breach proxy (0..100) using the forecast band
  - level:              "clear" | "warn" | "urgent" | "breached"

Honors a 3-calls-per-day free-tier cap (recorded in telemetry).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from milo_usage_forecaster import telemetry
from milo_usage_forecaster.forecast_engine import MonthlyForecast, forecast_from_logs


# Cap on free-tier daily invocations (per Trend Researcher spec).
FREE_DAILY_CAP = 3
TOOL_NAME = "budget_alert_check"


class BudgetCheck(BaseModel):
    """Output of budget_alert_check."""

    monthly_cap_usd: float
    spend_so_far_usd: float
    projected_end_of_month_usd: float
    headroom_usd: float = Field(..., description="cap - projected (negative = projected breach)")
    hours_until_breach: Optional[float] = Field(
        None,
        description="Hours at current rolling rate before crossing cap. None if no breach predicted.",
    )
    breach_risk_pct: float = Field(
        ..., description="0..100 — probability proxy based on forecast confidence band"
    )
    level: str = Field(..., description="'clear' | 'warn' | 'urgent' | 'breached'")
    rate_limit_remaining_today: int = Field(
        ..., description="Free-tier calls remaining today (3/day)"
    )
    note: str
    rate_limited: bool = False


def _level_from_risk(risk_pct: float, headroom: float) -> str:
    if headroom < 0:
        return "breached"
    if risk_pct >= 70:
        return "urgent"
    if risk_pct >= 30:
        return "warn"
    return "clear"


def _hours_until_breach(forecast_obj: MonthlyForecast, cap: float) -> Optional[float]:
    """How many hours at the rolling rate before spend crosses cap?

    Returns None when the cap won't be breached at all.
    """
    remaining_to_cap = cap - forecast_obj.spend_so_far_usd
    if remaining_to_cap <= 0:
        return 0.0
    daily_rate = forecast_obj.rolling_7day_avg_usd
    if daily_rate <= 0:
        return None
    days_left_at_rate = remaining_to_cap / daily_rate
    # If days-left-at-rate is more than the days remaining in the month, no breach.
    if days_left_at_rate >= forecast_obj.days_remaining and forecast_obj.projected_end_of_month_usd < cap:
        return None
    return round(days_left_at_rate * 24.0, 1)


def _risk_pct_from_forecast(forecast_obj: MonthlyForecast, cap: float) -> float:
    """Use the forecast confidence band as a proxy for breach probability.

    If even the LO of the band exceeds the cap, risk is ~95%.
    If the HI is below the cap, risk is ~5%.
    Otherwise interpolate linearly across the band.
    """
    lo = forecast_obj.confidence_lo_usd
    hi = forecast_obj.confidence_hi_usd
    if hi <= cap:
        return 5.0
    if lo >= cap:
        return 95.0
    if hi == lo:
        return 50.0
    # Where in [lo, hi] does cap fall? 0 = at lo (low risk), 1 = at hi (high risk).
    pos = (cap - lo) / (hi - lo)
    # Invert: low position = cap is near lo = cap is conservative = high risk of breach.
    risk = 100.0 * (1.0 - pos)
    return round(max(5.0, min(95.0, risk)), 1)


def check_budget(
    monthly_cap_usd: float,
    log_path: Optional[str] = None,
    *,
    enforce_rate_limit: bool = True,
) -> BudgetCheck:
    """Run a budget check. Wraps forecast_from_logs."""
    if monthly_cap_usd <= 0:
        return BudgetCheck(
            monthly_cap_usd=monthly_cap_usd,
            spend_so_far_usd=0.0,
            projected_end_of_month_usd=0.0,
            headroom_usd=0.0,
            hours_until_breach=None,
            breach_risk_pct=0.0,
            level="clear",
            rate_limit_remaining_today=FREE_DAILY_CAP,
            note="monthly_cap_usd must be > 0. Pass your real cap, e.g. 100.",
            rate_limited=False,
        )

    # Rate-limit check FIRST so we don't burn an expensive forecast for a capped caller.
    used_today = telemetry.get_daily_cap_count(TOOL_NAME)
    remaining = max(0, FREE_DAILY_CAP - used_today)
    if enforce_rate_limit and used_today >= FREE_DAILY_CAP:
        return BudgetCheck(
            monthly_cap_usd=monthly_cap_usd,
            spend_so_far_usd=0.0,
            projected_end_of_month_usd=0.0,
            headroom_usd=0.0,
            hours_until_breach=None,
            breach_risk_pct=0.0,
            level="clear",
            rate_limit_remaining_today=0,
            note=(
                f"Free-tier cap of {FREE_DAILY_CAP} calls/day reached for {TOOL_NAME}. "
                "Upgrade to Pro for unlimited budget checks + (v0.2) weekly digest."
            ),
            rate_limited=True,
        )

    forecast_obj = forecast_from_logs(log_path)
    headroom = round(monthly_cap_usd - forecast_obj.projected_end_of_month_usd, 2)
    hours = _hours_until_breach(forecast_obj, monthly_cap_usd)
    risk = _risk_pct_from_forecast(forecast_obj, monthly_cap_usd)
    level = _level_from_risk(risk, headroom)

    # Only count this call against the cap when it actually executed.
    if enforce_rate_limit:
        new_count = telemetry.increment_daily_cap(TOOL_NAME)
        remaining = max(0, FREE_DAILY_CAP - new_count)

    note = _build_note(level, headroom, hours, forecast_obj, monthly_cap_usd)
    return BudgetCheck(
        monthly_cap_usd=monthly_cap_usd,
        spend_so_far_usd=forecast_obj.spend_so_far_usd,
        projected_end_of_month_usd=forecast_obj.projected_end_of_month_usd,
        headroom_usd=headroom,
        hours_until_breach=hours,
        breach_risk_pct=risk,
        level=level,
        rate_limit_remaining_today=remaining,
        note=note,
        rate_limited=False,
    )


def _build_note(
    level: str,
    headroom: float,
    hours: Optional[float],
    forecast_obj: MonthlyForecast,
    cap: float,
) -> str:
    if level == "breached":
        return (
            f"Already projected over the ${cap:.0f} cap by ${-headroom:.2f}. "
            "Pause non-critical agent loops and run identify_spike_drivers to find the leak."
        )
    if level == "urgent":
        h = f"in ~{hours:.0f}h" if hours is not None else "soon"
        return (
            f"At current rate, you'll cross ${cap:.0f} {h}. Pull the trigger "
            "on the routing fix from milo-cost-auditor's get_pro_report — most "
            "teams cut burn 40-70% without losing quality."
        )
    if level == "warn":
        return (
            f"Trending toward the ${cap:.0f} cap. Projected ${forecast_obj.projected_end_of_month_usd:.2f}, "
            f"headroom ${headroom:.2f}. Re-check in 24h, or upgrade for the auto-digest."
        )
    return (
        f"Clear. Projected ${forecast_obj.projected_end_of_month_usd:.2f} vs cap ${cap:.0f} — "
        f"${headroom:.2f} headroom."
    )
