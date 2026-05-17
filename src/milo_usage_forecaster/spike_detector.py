"""
Spike-driver detector.

Given the last `lookback_days` of usage events, rank subagents / projects /
file-paths by token-per-day delta against the trailing average.

The output is a top-5 list of dimensions (mixing subagents, projects, files)
ranked by cost delta — the dev wants to know "who/what is burning my budget"
without picking a slicing dimension up front.

Pure compute on already-parsed UsageEvent records.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from milo_usage_forecaster.log_parser import (
    UsageEvent,
    collect_events,
    event_to_cost_usd,
    events_in_window,
    group_by_day,
)


class SpikeDriver(BaseModel):
    """One ranked driver of spend growth."""

    dimension: str = Field(..., description="'subagent' | 'project' | 'file'")
    label: str
    recent_avg_cost_per_day_usd: float
    trailing_avg_cost_per_day_usd: float
    cost_delta_per_day_usd: float
    cost_delta_pct: float = Field(
        ..., description="Percent change vs trailing avg (positive = grew)"
    )
    recent_total_tokens: int
    why: str


# How many days are "recent" inside the lookback window (the spike candidate).
_RECENT_FRACTION = 1.0 / 3.0  # last third = recent; prior two thirds = trailing


def _bucket_costs_per_day(events: List[UsageEvent]) -> Dict[str, float]:
    """Sum cost per day from a flat event list."""
    by_day_events = group_by_day(events)
    out: Dict[str, float] = {}
    for day, evs in by_day_events.items():
        out[day] = sum(event_to_cost_usd(ev) for ev in evs)
    return out


def _split_recent_trailing(
    events: List[UsageEvent], lookback_days: int
) -> Tuple[List[UsageEvent], List[UsageEvent], int, int]:
    """Split events into (recent, trailing) chunks. Returns recent_days, trailing_days too."""
    if not events:
        return [], [], 1, 1
    recent_days = max(1, int(round(lookback_days * _RECENT_FRACTION)))
    trailing_days = max(1, lookback_days - recent_days)
    now = datetime.now(tz=timezone.utc)
    recent_cutoff = now.timestamp() - recent_days * 86400.0
    trailing_cutoff = now.timestamp() - lookback_days * 86400.0
    recent: List[UsageEvent] = []
    trailing: List[UsageEvent] = []
    for ev in events:
        try:
            ts = datetime.fromisoformat(ev.timestamp.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            continue
        if ts >= recent_cutoff:
            recent.append(ev)
        elif ts >= trailing_cutoff:
            trailing.append(ev)
    return recent, trailing, recent_days, trailing_days


def _aggregate_by_dim(events: List[UsageEvent], dim: str) -> Dict[str, Dict[str, float]]:
    """Group events by dim and aggregate {cost_sum, token_sum, event_count}."""
    out: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"cost_sum": 0.0, "token_sum": 0.0, "event_count": 0.0}
    )
    for ev in events:
        if dim == "subagent":
            key = ev.subagent
        elif dim == "project":
            key = ev.project
        elif dim == "file":
            key = ev.file
        else:
            continue
        cost = event_to_cost_usd(ev)
        out[key]["cost_sum"] += cost
        out[key]["token_sum"] += ev.total_input_tokens + ev.output_tokens
        out[key]["event_count"] += 1.0
    return out


def _build_drivers(
    recent_agg: Dict[str, Dict[str, float]],
    trailing_agg: Dict[str, Dict[str, float]],
    dim: str,
    recent_days: int,
    trailing_days: int,
) -> List[SpikeDriver]:
    """Compare recent vs trailing per-day cost rates for one dimension."""
    drivers: List[SpikeDriver] = []
    for key, recent in recent_agg.items():
        recent_per_day = recent["cost_sum"] / max(1, recent_days)
        trailing_data = trailing_agg.get(key, {"cost_sum": 0.0, "token_sum": 0.0})
        trailing_per_day = trailing_data["cost_sum"] / max(1, trailing_days)
        delta_per_day = recent_per_day - trailing_per_day
        if trailing_per_day > 0:
            delta_pct = 100.0 * delta_per_day / trailing_per_day
        elif recent_per_day > 0:
            delta_pct = 100.0  # brand new dimension
        else:
            delta_pct = 0.0
        # Skip noise.
        if recent_per_day < 0.001 and trailing_per_day < 0.001:
            continue
        why = _explain_driver(
            dim=dim, label=key,
            recent_per_day=recent_per_day, trailing_per_day=trailing_per_day,
        )
        drivers.append(SpikeDriver(
            dimension=dim,
            label=str(key) or "(unknown)",
            recent_avg_cost_per_day_usd=round(recent_per_day, 4),
            trailing_avg_cost_per_day_usd=round(trailing_per_day, 4),
            cost_delta_per_day_usd=round(delta_per_day, 4),
            cost_delta_pct=round(delta_pct, 1),
            recent_total_tokens=int(recent["token_sum"]),
            why=why,
        ))
    return drivers


def _explain_driver(*, dim: str, label: str, recent_per_day: float, trailing_per_day: float) -> str:
    if trailing_per_day == 0 and recent_per_day > 0:
        return (
            f"New {dim} this window — first appearance on the spend curve. "
            f"~${recent_per_day:.3f}/day burn."
        )
    growth_pct = 100.0 * (recent_per_day - trailing_per_day) / max(trailing_per_day, 1e-9)
    if growth_pct > 100:
        adjective = "doubled+"
    elif growth_pct > 50:
        adjective = "spiked"
    elif growth_pct > 10:
        adjective = "trending up"
    elif growth_pct < -50:
        adjective = "cooled off"
    else:
        adjective = "flat"
    return (
        f"{dim.capitalize()} `{label}` {adjective}: "
        f"recent ${recent_per_day:.3f}/day vs trailing ${trailing_per_day:.3f}/day."
    )


def detect_spikes(events: List[UsageEvent], *, lookback_days: int = 7) -> List[SpikeDriver]:
    """Return top-5 spike drivers across subagent/project/file dimensions."""
    if lookback_days < 2:
        lookback_days = 2
    window = events_in_window(events, days=lookback_days)
    recent_evs, trailing_evs, recent_days, trailing_days = _split_recent_trailing(
        window, lookback_days
    )
    all_drivers: List[SpikeDriver] = []
    for dim in ("subagent", "project", "file"):
        recent_agg = _aggregate_by_dim(recent_evs, dim)
        trailing_agg = _aggregate_by_dim(trailing_evs, dim)
        all_drivers.extend(_build_drivers(
            recent_agg, trailing_agg, dim, recent_days, trailing_days,
        ))
    # Rank by absolute cost delta per day (positive deltas first because those
    # are the ones the dev needs to know about; ties broken by total tokens).
    all_drivers.sort(
        key=lambda d: (d.cost_delta_per_day_usd, d.recent_total_tokens),
        reverse=True,
    )
    return all_drivers[:5]


def detect_spikes_from_logs(
    log_path: Optional[str] = None,
    lookback_days: int = 7,
) -> List[SpikeDriver]:
    """High-level entry: discover logs, parse, detect."""
    events = collect_events(log_path)
    return detect_spikes(events, lookback_days=lookback_days)
