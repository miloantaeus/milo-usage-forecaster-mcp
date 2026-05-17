"""
Optimization recommender (PRO tier).

Free tier returns 1 generic tip + a payment_request envelope.
Pro tier (gated on valid HMAC pro_key) returns:

  - model_routing:     per-current-model swap recommendations with $/mo savings
  - caching_advice:    prompt-cache hit-rate analysis + projected savings
  - compaction_advice: long-context recommendations (when avg input > 10k tokens)
  - projected_total_savings_usd: monthly $ saved if all recommendations applied

All computation runs locally on parsed UsageEvent records — no network calls.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from milo_usage_forecaster import payment
from milo_usage_forecaster.log_parser import (
    UsageEvent,
    collect_events,
    event_to_cost_usd,
    events_in_window,
)
from milo_usage_forecaster.pricing_table import (
    CACHE_READ_MULTIPLIER,
    ModelPrice,
    cheaper_than,
    lookup,
)


# ---- public schemas --------------------------------------------------------


class RoutingRecommendation(BaseModel):
    current_model: str
    suggested_model: str
    monthly_calls: int
    current_monthly_cost_usd: float
    projected_monthly_cost_usd: float
    monthly_savings_usd: float
    quality_band_delta: int
    rationale: str


class CachingRecommendation(BaseModel):
    current_cache_hit_pct: float
    target_cache_hit_pct: float
    monthly_savings_usd: float
    rationale: str
    fix_summary: str


class CompactionRecommendation(BaseModel):
    avg_input_tokens: int
    monthly_cost_attributable_to_long_context_usd: float
    monthly_savings_usd: float
    fix_summary: str


class OptimizationPlan(BaseModel):
    tier: str = Field(..., description="'free' | 'pro' | 'pro-year'")
    paid: bool = Field(..., description="True if pro_key validated, False otherwise")
    sample_event_count: int
    model_routing: List[RoutingRecommendation] = Field(default_factory=list)
    caching_advice: Optional[CachingRecommendation] = None
    compaction_advice: Optional[CompactionRecommendation] = None
    projected_total_savings_usd: float = 0.0
    generic_tip: Optional[str] = Field(
        None,
        description="Single free-tier tip shown when pro_key absent",
    )
    payment_request: Optional[Dict[str, Any]] = None
    pro_key_expires_at: Optional[str] = None
    note: str


# ---- routing analysis ------------------------------------------------------


def _routing_recommendations(events: List[UsageEvent]) -> List[RoutingRecommendation]:
    """Per-model: if a cheaper viable peer exists, project savings if everyone switched."""
    if not events:
        return []
    by_model: Dict[str, List[UsageEvent]] = defaultdict(list)
    for ev in events:
        by_model[ev.model].append(ev)
    recs: List[RoutingRecommendation] = []
    for model_name, evs in by_model.items():
        price = lookup(model_name)
        if price is None:
            continue
        peers = cheaper_than(price, min_quality=3)
        if not peers:
            continue
        alt = peers[0]
        current_cost = sum(event_to_cost_usd(ev) for ev in evs)
        alt_cost = sum(
            _alt_cost_for_event(ev, alt) for ev in evs
        )
        if alt_cost >= current_cost:
            continue
        savings = current_cost - alt_cost
        recs.append(RoutingRecommendation(
            current_model=model_name,
            suggested_model=f"{alt.provider}/{alt.model}",
            monthly_calls=len(evs),
            current_monthly_cost_usd=round(current_cost, 2),
            projected_monthly_cost_usd=round(alt_cost, 2),
            monthly_savings_usd=round(savings, 2),
            quality_band_delta=price.quality_band - alt.quality_band,
            rationale=_routing_rationale(price, alt, len(evs)),
        ))
    recs.sort(key=lambda r: r.monthly_savings_usd, reverse=True)
    return recs


def _alt_cost_for_event(ev: UsageEvent, alt: ModelPrice) -> float:
    """Project cost if this event had hit `alt` instead. Cache reads still discounted."""
    fresh_in = (ev.input_tokens / 1_000_000.0) * alt.input_per_million
    cache_create = (
        (ev.cache_creation_input_tokens / 1_000_000.0)
        * alt.input_per_million
        * 1.25  # creation multiplier
    )
    cache_read = (
        (ev.cache_read_input_tokens / 1_000_000.0)
        * alt.input_per_million
        * CACHE_READ_MULTIPLIER
    )
    output = (ev.output_tokens / 1_000_000.0) * alt.output_per_million
    return fresh_in + cache_create + cache_read + output


def _routing_rationale(current: ModelPrice, alt: ModelPrice, n_calls: int) -> str:
    drop = current.quality_band - alt.quality_band
    if drop == 0:
        return (
            f"{n_calls} calls hit {current.model} on a workload where {alt.model} "
            "has the same quality band — pure pricing arbitrage."
        )
    if drop == 1:
        return (
            f"{n_calls} calls hit {current.model}. 1-band drop to {alt.model} is "
            "usually invisible on routine code/summarization; escalate to current on retry."
        )
    return (
        f"{n_calls} calls hit {current.model}. {drop}-band drop to {alt.model} — "
        "only safe for tightly scoped extraction/classify calls."
    )


# ---- caching analysis ------------------------------------------------------


def _caching_recommendation(events: List[UsageEvent]) -> Optional[CachingRecommendation]:
    """If cache hit rate is low + Anthropic-class costs are involved, suggest caching."""
    if not events:
        return None
    total_input = sum(ev.total_input_tokens for ev in events)
    if total_input == 0:
        return None
    cache_reads = sum(ev.cache_read_input_tokens for ev in events)
    cache_hit_pct = 100.0 * cache_reads / total_input
    if cache_hit_pct >= 60:
        # Already well-cached — diminishing returns.
        return None
    target = 60.0  # aim for 60% hit rate as a healthy steady state
    # Project monthly savings assuming we converted the gap between current and target
    # cache-hit on fresh input tokens. Fresh -> cache-read at 0.10x cost.
    gap_pct = (target - cache_hit_pct) / 100.0
    convertible_tokens = sum(ev.input_tokens for ev in events) * gap_pct
    # Weighted-avg input price across events (rough)
    prices: List[float] = []
    for ev in events:
        p = lookup(ev.model)
        if p is not None:
            prices.append(p.input_per_million)
    avg_input_per_million = statistics.mean(prices) if prices else 0.0
    fresh_cost_today = (convertible_tokens / 1_000_000.0) * avg_input_per_million
    cached_cost_target = fresh_cost_today * CACHE_READ_MULTIPLIER
    savings = round(fresh_cost_today - cached_cost_target, 2)
    if savings < 1.0:
        return None
    return CachingRecommendation(
        current_cache_hit_pct=round(cache_hit_pct, 1),
        target_cache_hit_pct=target,
        monthly_savings_usd=savings,
        rationale=(
            f"Cache-read tokens are {cache_hit_pct:.1f}% of total input. Anthropic "
            "and OpenAI both charge ~10% of input price for cache reads vs full "
            "price for fresh input."
        ),
        fix_summary=(
            "Pin system prompts, RAG context, and large tool definitions to the "
            "front of the prompt so prompt-caching kicks in. Target 60%+ cache reads."
        ),
    )


# ---- compaction analysis ---------------------------------------------------


def _compaction_recommendation(events: List[UsageEvent]) -> Optional[CompactionRecommendation]:
    """When avg input is >10k tokens, suggest summarize-and-checkpoint compaction."""
    if not events:
        return None
    avg_input = statistics.mean(ev.total_input_tokens for ev in events)
    if avg_input < 10_000:
        return None
    # Cost attributable to the long-context portion (input > 10k tokens band).
    long_context_cost = 0.0
    for ev in events:
        if ev.total_input_tokens > 10_000:
            long_context_cost += event_to_cost_usd(ev)
    # Assume compaction halves the average input on long-context calls.
    estimated_savings = round(long_context_cost * 0.5, 2)
    if estimated_savings < 1.0:
        return None
    return CompactionRecommendation(
        avg_input_tokens=int(avg_input),
        monthly_cost_attributable_to_long_context_usd=round(long_context_cost, 2),
        monthly_savings_usd=estimated_savings,
        fix_summary=(
            "Add a 50%-context-window compaction trigger: when the conversation "
            "buffer hits half the model's context, summarize the oldest half via "
            "claude-haiku-4.5 / gemini-3-flash and replace it. Cuts input cost ~50%."
        ),
    )


# ---- public API ------------------------------------------------------------


def _generic_free_tip() -> str:
    return (
        "Free tip: most teams cut LLM bills 40-60% just by downrouting "
        "summarization + agent-loop calls to claude-haiku-4.5 or gemini-3-flash. "
        "Upgrade to Pro ($19/mo) for per-model routing recommendations with "
        "actual $/mo savings projections from your real usage data."
    )


def recommend(
    events: List[UsageEvent],
    pro_key: Optional[str] = None,
) -> OptimizationPlan:
    """Build an OptimizationPlan. Free tier returns generic tip + payment_request."""
    window = events_in_window(events, days=30)
    validation = payment.validate_pro_key(pro_key) if pro_key else None
    if validation is None or not validation.valid:
        req = payment.build_payment_request("pro")
        return OptimizationPlan(
            tier="free",
            paid=False,
            sample_event_count=len(window),
            generic_tip=_generic_free_tip(),
            payment_request=req.model_dump(),
            note=(
                "Free tier returns 1 generic tip. The pro tier ($19/mo) returns "
                "per-model routing recommendations, caching advice, and compaction "
                "advice with $/mo savings projections from your real usage."
            ),
        )

    routing = _routing_recommendations(window)
    caching = _caching_recommendation(window)
    compaction = _compaction_recommendation(window)
    total_savings = round(
        sum(r.monthly_savings_usd for r in routing)
        + (caching.monthly_savings_usd if caching else 0.0)
        + (compaction.monthly_savings_usd if compaction else 0.0),
        2,
    )
    note = (
        "Pro report: applied all recommendations would cut ~"
        f"${total_savings:.2f}/mo from your projected spend. Re-run after 30 days "
        "to verify the savings landed."
    )
    return OptimizationPlan(
        tier=validation.tier or "pro",
        paid=True,
        sample_event_count=len(window),
        model_routing=routing,
        caching_advice=caching,
        compaction_advice=compaction,
        projected_total_savings_usd=total_savings,
        pro_key_expires_at=validation.expires_at,
        note=note,
    )


def recommend_from_logs(
    log_path: Optional[str] = None,
    pro_key: Optional[str] = None,
) -> OptimizationPlan:
    """High-level entry: discover logs, parse, recommend."""
    events = collect_events(log_path)
    return recommend(events, pro_key=pro_key)
