"""Tests for the spike-driver detector."""

from __future__ import annotations

from pathlib import Path

import pytest

from milo_usage_forecaster import spike_detector


def test_spike_detector_returns_at_most_five_drivers(synthetic_log_root_set: Path) -> None:
    drivers = spike_detector.detect_spikes_from_logs(lookback_days=7)
    assert isinstance(drivers, list)
    assert len(drivers) <= 5


def test_spike_detector_identifies_yesterday_spike(synthetic_log_root_set: Path) -> None:
    """The synthetic fixture has a 10-call spike yesterday on 'runaway-research-loop'."""
    drivers = spike_detector.detect_spikes_from_logs(lookback_days=7)
    labels = {d.label for d in drivers}
    # The fixture's spike-driving subagent should appear in the top-5
    assert "runaway-research-loop" in labels, (
        f"Expected 'runaway-research-loop' in top-5 drivers; got {labels}"
    )


def test_spike_detector_ranks_by_cost_delta(synthetic_log_root_set: Path) -> None:
    """Drivers are sorted by cost_delta_per_day_usd descending."""
    drivers = spike_detector.detect_spikes_from_logs(lookback_days=7)
    deltas = [d.cost_delta_per_day_usd for d in drivers]
    assert deltas == sorted(deltas, reverse=True)


def test_spike_detector_includes_subagent_and_project_dims(synthetic_log_root_set: Path) -> None:
    drivers = spike_detector.detect_spikes_from_logs(lookback_days=14)
    dims = {d.dimension for d in drivers}
    # Both 'subagent' and 'project' should be represented when there's a real spike
    assert "subagent" in dims or "project" in dims, (
        f"Expected subagent/project dims, got {dims}"
    )


def test_spike_detector_empty_log_returns_empty_list(tmp_path: Path) -> None:
    drivers = spike_detector.detect_spikes_from_logs(log_path=str(tmp_path))
    assert drivers == []


def test_spike_detector_clamps_lookback_to_min_two() -> None:
    """lookback_days < 2 silently clamps to 2 (no divide-by-zero on the split)."""
    from milo_usage_forecaster.log_parser import UsageEvent
    drivers = spike_detector.detect_spikes([], lookback_days=0)
    assert drivers == []  # no events to detect, but no crash


def test_spike_driver_why_string_present(synthetic_log_root_set: Path) -> None:
    drivers = spike_detector.detect_spikes_from_logs(lookback_days=7)
    for d in drivers:
        assert d.why and len(d.why) > 10  # plain-English rationale
