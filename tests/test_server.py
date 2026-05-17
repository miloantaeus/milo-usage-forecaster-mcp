"""MCP protocol smoke test + tool-layer integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from milo_usage_forecaster import payment, server, telemetry, tools


def test_build_server_constructs() -> None:
    """FastMCP server constructs without error and exposes the right name."""
    mcp = server.build_server()
    assert mcp.name == server.SERVER_NAME
    assert server.SERVER_INSTRUCTIONS in (mcp.instructions or "")


@pytest.mark.asyncio
async def test_server_lists_all_four_tools() -> None:
    """list_tools should return exactly the four tools we registered."""
    mcp = server.build_server()
    tool_list = await mcp.list_tools()
    names = sorted(t.name for t in tool_list)
    assert names == [
        "budget_alert_check",
        "forecast_monthly_spend",
        "identify_spike_drivers",
        "optimize_recommendations",
    ]


@pytest.mark.asyncio
async def test_each_tool_has_input_schema() -> None:
    """Every registered tool must publish a usable inputSchema."""
    mcp = server.build_server()
    tool_list = await mcp.list_tools()
    for t in tool_list:
        assert t.inputSchema is not None
        assert "properties" in t.inputSchema
        assert t.description


def test_forecast_monthly_spend_tool(synthetic_log_root_set: Path) -> None:
    out = tools.forecast_monthly_spend_tool()
    assert isinstance(out, dict)
    assert "month_label" in out
    assert "projected_end_of_month_usd" in out
    assert "confidence_lo_usd" in out
    assert "confidence_hi_usd" in out
    assert out["confidence_lo_usd"] <= out["confidence_mid_usd"] <= out["confidence_hi_usd"]


def test_identify_spike_drivers_tool(synthetic_log_root_set: Path) -> None:
    out = tools.identify_spike_drivers_tool(lookback_days=7)
    assert "drivers" in out
    assert isinstance(out["drivers"], list)
    assert len(out["drivers"]) <= 5


def test_budget_alert_check_tool(synthetic_log_root_set: Path) -> None:
    out = tools.budget_alert_check_tool(monthly_cap_usd=100.0)
    assert "level" in out
    assert out["level"] in {"clear", "warn", "urgent", "breached"}
    assert "rate_limit_remaining_today" in out


def test_optimize_recommendations_tool_no_key(synthetic_log_root_set: Path) -> None:
    out = tools.optimize_recommendations_tool()
    assert out["paid"] is False
    assert out["tier"] == "free"
    assert out["payment_request"]["http_status"] == 402


def test_optimize_recommendations_tool_with_key(synthetic_log_root_set: Path) -> None:
    token = payment.issue_pro_key("pro", "2099-01-01T00:00:00Z")
    out = tools.optimize_recommendations_tool(pro_key=token)
    assert out["paid"] is True
    assert out["tier"] == "pro"


def test_telemetry_records_invocations(synthetic_log_root_set: Path) -> None:
    tools.forecast_monthly_spend_tool()
    tools.forecast_monthly_spend_tool()
    tools.identify_spike_drivers_tool()
    counts = telemetry.get_counts()
    assert counts["forecast_monthly_spend"] >= 2
    assert counts["identify_spike_drivers"] >= 1


def test_install_id_persists() -> None:
    first = telemetry.install_id()
    second = telemetry.install_id()
    assert first == second
    assert len(first) > 8


def test_first_invocation_banner_then_silent() -> None:
    msg1 = telemetry.first_invocation_banner()
    msg2 = telemetry.first_invocation_banner()
    assert msg1 is not None
    assert "v0.1" in msg1
    assert "local" in msg1
    assert msg2 is None
