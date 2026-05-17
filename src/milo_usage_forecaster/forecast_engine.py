"""
Monthly-spend forecaster.

Approach (kept deterministic + no-deps):
  1. Parse the last 30 days of usage events into per-day cost.
  2. Compute a 7-day trailing average to smooth weekend dips.
  3. Apply a day-of-week seasonality multiplier: per-DoW factor relative to
     the trailing-avg baseline (so a quiet Saturday doesn't drag the projection).
  4. Project across the remaining days of the current month.
  5. Confidence band (lo/mid/hi) = +/- 1 std-dev of daily costs over the
     window, scaled by remaining days.

This is intentionally simple. The point isn't to win a forecasting bake-off —
it's to give a dev a reasonable "you're heading for ~$X this month, +/- $Y" in
under 100ms so they can react. v0.2 swaps in Holt-Winters if there's demand.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from pydantic import BaseModel, Field

from milo_usage_forecaster.log_parser import (
    UsageEvent,
    collect_events,
    event_to_cost_usd,
    events_in_window,
    group_by_day,
)


class MonthlyForecast(BaseModel):
    """Output of forecast_monthly_spend."""

    month_label: str = Field(..., description="e.g. '2026-05'")
    days_in_month: int
    days_elapsed: int
    days_remaining: int
    spend_so_far_usd: float
    projected_end_of_month_usd: float
    confidence_lo_usd: float
    confidence_mid_usd: float
    confidence_hi_usd: float
    daily_avg_usd: float
    rolling_7day_avg_usd: float
    sample_event_count: int
    method: str = Field(
        "rolling-7d-avg+dow-seasonality",
        description="Forecast method ID for downstream consumers",
    )
    note: str = Field(
        ...,
        description=(
            "Plain-English context: what the projection is based on and "
            "what the caller should do with it."
        ),
    )


# ---- core math ------------------------------------------------------------


def _today_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _month_meta(now: Optional[datetime] = None) -> tuple:
    """Return (year, month, days_in_month, days_elapsed) for the current UTC month."""
    now = now or _today_utc()
    year = now.year
    month = now.month
    if month == 12:
        next_month_start = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month_start = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    this_month_start = datetime(year, month, 1, tzinfo=timezone.utc)
    days_in_month = (next_month_start - this_month_start).days
    days_elapsed = (now - this_month_start).days + 1  # inclusive of today
    return year, month, days_in_month, days_elapsed


def _dow_seasonality(daily_costs: dict) -> dict:
    """For each day-of-week (0=Mon..6=Sun), compute mean cost / overall mean.

    Returns {dow: multiplier}. Missing DoWs default to 1.0.
    """
    if not daily_costs:
        return {}
    by_dow: dict = {}
    for day_str, cost in daily_costs.items():
        try:
            dt = datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        by_dow.setdefault(dt.weekday(), []).append(cost)
    overall = statistics.mean(daily_costs.values()) if daily_costs else 0.0
    if overall <= 0:
        return {dow: 1.0 for dow in range(7)}
    return {dow: (statistics.mean(vals) / overall) for dow, vals in by_dow.items()}


def _rolling_7day_avg(daily_costs: dict, end_day: datetime) -> float:
    """Mean cost over the 7 days ending at end_day."""
    if not daily_costs:
        return 0.0
    cutoff = end_day - timedelta(days=7)
    recent = [
        cost
        for day_str, cost in daily_costs.items()
        if cutoff <= datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=timezone.utc) <= end_day
    ]
    if not recent:
        return statistics.mean(daily_costs.values())
    return statistics.mean(recent)


def forecast(
    events: List[UsageEvent],
    *,
    current_month_days_so_far: Optional[int] = None,
    now: Optional[datetime] = None,
) -> MonthlyForecast:
    """Build a MonthlyForecast from raw usage events."""
    now = now or _today_utc()
    year, month, days_in_month, default_elapsed = _month_meta(now)
    days_elapsed = current_month_days_so_far or default_elapsed
    days_elapsed = max(1, min(days_in_month, days_elapsed))
    days_remaining = max(0, days_in_month - days_elapsed)

    # Build per-day cost map (last 30 days for the projection inputs).
    window = events_in_window(events, days=30)
    cost_per_event = [(ev, event_to_cost_usd(ev)) for ev in window]
    by_day_events = group_by_day([ev for ev, _ in cost_per_event])
    daily_cost: dict = {}
    cost_index = {id(ev): c for ev, c in cost_per_event}
    for day, evs in by_day_events.items():
        daily_cost[day] = round(sum(cost_index.get(id(ev), 0.0) for ev in evs), 6)

    # Spend so far (current month only)
    this_month_start = datetime(year, month, 1, tzinfo=timezone.utc)
    spend_so_far = 0.0
    for day, cost in daily_cost.items():
        try:
            d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if d >= this_month_start:
            spend_so_far += cost

    rolling = _rolling_7day_avg(daily_cost, now)
    daily_avg = (
        statistics.mean(daily_cost.values()) if daily_cost else 0.0
    )
    dow_factors = _dow_seasonality(daily_cost)

    # Project remaining days using rolling avg * DoW factor for each remaining day.
    projected_remaining = 0.0
    for offset in range(1, days_remaining + 1):
        future_day = now + timedelta(days=offset)
        factor = dow_factors.get(future_day.weekday(), 1.0)
        projected_remaining += rolling * factor
    projected_eom = round(spend_so_far + projected_remaining, 2)

    # Confidence band: +/- 1 std-dev of daily costs, scaled by remaining days.
    if len(daily_cost) >= 2:
        sigma = statistics.pstdev(daily_cost.values())
    else:
        sigma = rolling * 0.5
    band = sigma * math.sqrt(max(1, days_remaining))
    lo = round(max(0.0, projected_eom - band), 2)
    mid = projected_eom
    hi = round(projected_eom + band, 2)

    note = _build_note(
        spend_so_far=spend_so_far,
        projected=projected_eom,
        days_elapsed=days_elapsed,
        days_in_month=days_in_month,
        rolling=rolling,
        sample_count=len(window),
    )

    return MonthlyForecast(
        month_label=f"{year:04d}-{month:02d}",
        days_in_month=days_in_month,
        days_elapsed=days_elapsed,
        days_remaining=days_remaining,
        spend_so_far_usd=round(spend_so_far, 2),
        projected_end_of_month_usd=projected_eom,
        confidence_lo_usd=lo,
        confidence_mid_usd=mid,
        confidence_hi_usd=hi,
        daily_avg_usd=round(daily_avg, 4),
        rolling_7day_avg_usd=round(rolling, 4),
        sample_event_count=len(window),
        note=note,
    )


def _build_note(
    *,
    spend_so_far: float,
    projected: float,
    days_elapsed: int,
    days_in_month: int,
    rolling: float,
    sample_count: int,
) -> str:
    if sample_count == 0:
        return (
            "No usage events found in the last 30 days. Point me at a real log "
            "file via log_path, or wait until your editor writes ~/.claude/projects."
        )
    if sample_count < 20:
        return (
            f"Only {sample_count} usage events parsed in 30d — the projection is "
            f"shaky. Treat the confidence band as the real signal until you have "
            "at least a week of consistent data."
        )
    pct_elapsed = (days_elapsed / days_in_month) * 100.0
    pct_spent = (spend_so_far / projected) * 100.0 if projected > 0 else 0.0
    pace = "on pace" if abs(pct_spent - pct_elapsed) < 10 else (
        "ahead of pace" if pct_spent > pct_elapsed else "behind pace"
    )
    return (
        f"Day {days_elapsed} of {days_in_month}. You're {pace} — spent "
        f"${spend_so_far:.2f} ({pct_spent:.0f}%), projected ${projected:.2f}. "
        f"7-day rolling avg = ${rolling:.2f}/day."
    )


# ---- public API ------------------------------------------------------------


def forecast_from_logs(
    log_path: Optional[str] = None,
    current_month_days_so_far: Optional[int] = None,
) -> MonthlyForecast:
    """High-level entry: discover logs, parse, forecast."""
    events = collect_events(log_path)
    return forecast(events, current_month_days_so_far=current_month_days_so_far)
