"""
The four MCP tools exposed by this server.

Each tool function is plain Python returning a pydantic BaseModel-derived JSON
dict, so the layer is testable without standing up the MCP loop.

server.py registers these on the FastMCP instance.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from milo_usage_forecaster import (
    budget_alert,
    forecast_engine,
    optimization_recommender,
    spike_detector,
    telemetry,
)


# ---- re-exported schemas (for downstream introspection) -------------------

MonthlyForecast = forecast_engine.MonthlyForecast
SpikeDriver = spike_detector.SpikeDriver
BudgetCheck = budget_alert.BudgetCheck
OptimizationPlan = optimization_recommender.OptimizationPlan


# ---- tool bodies ----------------------------------------------------------


def forecast_monthly_spend_tool(
    log_path: Optional[str] = None,
    current_month_days_so_far: Optional[int] = None,
) -> Dict[str, Any]:
    """forecast_monthly_spend MCP tool body. Returns MonthlyForecast as dict."""
    telemetry.record_invocation("forecast_monthly_spend")
    out = forecast_engine.forecast_from_logs(
        log_path=log_path,
        current_month_days_so_far=current_month_days_so_far,
    )
    return out.model_dump()


def identify_spike_drivers_tool(
    log_path: Optional[str] = None,
    lookback_days: int = 7,
) -> Dict[str, Any]:
    """identify_spike_drivers MCP tool body. Returns list of SpikeDriver as dicts."""
    telemetry.record_invocation("identify_spike_drivers")
    drivers = spike_detector.detect_spikes_from_logs(
        log_path=log_path,
        lookback_days=lookback_days,
    )
    return {"drivers": [d.model_dump() for d in drivers]}


def budget_alert_check_tool(
    monthly_cap_usd: float,
    log_path: Optional[str] = None,
) -> Dict[str, Any]:
    """budget_alert_check MCP tool body. Returns BudgetCheck as dict.

    Telemetry note: invocation is always recorded; the rate-limit cap on
    free tier is tracked separately inside budget_alert.check_budget so that
    a rate-limited reply doesn't double-count against the user.
    """
    telemetry.record_invocation("budget_alert_check")
    out = budget_alert.check_budget(monthly_cap_usd, log_path=log_path)
    return out.model_dump()


def optimize_recommendations_tool(
    log_path: Optional[str] = None,
    pro_key: Optional[str] = None,
) -> Dict[str, Any]:
    """optimize_recommendations MCP tool body. Returns OptimizationPlan as dict.

    Free tier returns a generic tip + x402 payment_request envelope.
    Pro tier (validated HMAC pro_key) returns full per-model recommendations.
    """
    telemetry.record_invocation("optimize_recommendations")
    out = optimization_recommender.recommend_from_logs(
        log_path=log_path,
        pro_key=pro_key,
    )
    return out.model_dump()
