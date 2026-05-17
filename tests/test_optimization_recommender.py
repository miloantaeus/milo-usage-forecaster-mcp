"""Tests for the pro-tier optimization recommender."""

from __future__ import annotations

from pathlib import Path

import pytest

from milo_usage_forecaster import optimization_recommender, payment


def test_optimize_no_key_returns_payment_request(synthetic_log_root_set: Path) -> None:
    out = optimization_recommender.recommend_from_logs(pro_key=None)
    assert out.paid is False
    assert out.tier == "free"
    assert out.generic_tip is not None
    assert out.payment_request is not None
    assert out.payment_request["http_status"] == 402
    assert out.payment_request["tier"] == "pro"
    assert out.payment_request["payment_url"].startswith("https://store-v2-khaki.vercel.app")
    assert out.projected_total_savings_usd == 0.0
    assert out.model_routing == []


def test_optimize_invalid_key_returns_payment_request(synthetic_log_root_set: Path) -> None:
    out = optimization_recommender.recommend_from_logs(pro_key="bogus.signature")
    assert out.paid is False
    assert out.payment_request is not None


def test_optimize_with_valid_pro_key(synthetic_log_root_set: Path) -> None:
    token = payment.issue_pro_key("pro", "2099-01-01T00:00:00Z")
    out = optimization_recommender.recommend_from_logs(pro_key=token)
    assert out.paid is True
    assert out.tier == "pro"
    assert out.pro_key_expires_at == "2099-01-01T00:00:00Z"
    # With the synthetic fixture, opus calls should produce at least one routing rec
    assert len(out.model_routing) >= 1
    # Projected total savings is a non-negative number
    assert out.projected_total_savings_usd >= 0.0


def test_optimize_with_pro_year_key(synthetic_log_root_set: Path) -> None:
    token = payment.issue_pro_key("pro-year", "2099-01-01T00:00:00Z")
    out = optimization_recommender.recommend_from_logs(pro_key=token)
    assert out.paid is True
    assert out.tier == "pro-year"


def test_optimize_routing_recs_ranked_by_savings(synthetic_log_root_set: Path) -> None:
    token = payment.issue_pro_key("pro", "2099-01-01T00:00:00Z")
    out = optimization_recommender.recommend_from_logs(pro_key=token)
    savings = [r.monthly_savings_usd for r in out.model_routing]
    assert savings == sorted(savings, reverse=True)


def test_optimize_caching_recommendation_when_cache_hit_low() -> None:
    """Construct events with low cache hit % to exercise the caching rec path."""
    from datetime import datetime, timezone, timedelta
    from milo_usage_forecaster.log_parser import UsageEvent
    now = datetime.now(tz=timezone.utc)
    # 30 events of fresh-input-heavy claude-opus-4-7 work, zero cache reads
    events = [
        UsageEvent(
            timestamp=(now - timedelta(days=d % 30 + 1)).isoformat().replace("+00:00", "Z"),
            model="claude-opus-4-7",
            project="p", file="f",
            input_tokens=20_000, output_tokens=500,
            cache_read_input_tokens=0,
        ) for d in range(30)
    ]
    token = payment.issue_pro_key("pro", "2099-01-01T00:00:00Z")
    out = optimization_recommender.recommend(events, pro_key=token)
    assert out.paid is True
    assert out.caching_advice is not None
    assert out.caching_advice.current_cache_hit_pct < 60.0
    assert out.caching_advice.monthly_savings_usd > 0


def test_optimize_compaction_recommendation_when_long_context() -> None:
    """Construct events with avg input > 10k to exercise the compaction rec path."""
    from datetime import datetime, timezone, timedelta
    from milo_usage_forecaster.log_parser import UsageEvent
    now = datetime.now(tz=timezone.utc)
    events = [
        UsageEvent(
            timestamp=(now - timedelta(days=d % 30 + 1)).isoformat().replace("+00:00", "Z"),
            model="claude-opus-4-7",
            project="p", file="f",
            input_tokens=60_000, output_tokens=500,
        ) for d in range(20)
    ]
    token = payment.issue_pro_key("pro", "2099-01-01T00:00:00Z")
    out = optimization_recommender.recommend(events, pro_key=token)
    assert out.compaction_advice is not None
    assert out.compaction_advice.avg_input_tokens >= 10_000
    assert out.compaction_advice.monthly_savings_usd > 0


def test_optimize_empty_events_with_valid_key_returns_paid_but_empty() -> None:
    """Valid key + no usage → paid=True, but no recommendations to give."""
    token = payment.issue_pro_key("pro", "2099-01-01T00:00:00Z")
    out = optimization_recommender.recommend([], pro_key=token)
    assert out.paid is True
    assert out.sample_event_count == 0
    assert out.model_routing == []
    assert out.projected_total_savings_usd == 0.0
