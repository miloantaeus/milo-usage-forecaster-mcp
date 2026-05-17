"""
MCP server bootstrap.

Wires the four tool functions onto a FastMCP instance and starts the stdio loop.
Run via:
    python -m milo_usage_forecaster
    mcp-usage-forecaster    (console entry point)

Tested with the official Python MCP SDK (`mcp` package, >= 1.0).
"""

from __future__ import annotations

import sys
from typing import Any, Dict, Optional

from milo_usage_forecaster import __version__, payment, telemetry, tools

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - hard import-time error
    sys.stderr.write(
        "milo-usage-forecaster needs the `mcp` package (pip install mcp).\n"
        f"Import error: {exc}\n"
    )
    raise


SERVER_NAME = "milo-usage-forecaster"
SERVER_INSTRUCTIONS = (
    "Milo Usage Forecaster — I predict your LLM spend before it spikes. Point "
    "me at your local Claude Code logs (default: ~/.claude/projects) and I'll "
    "project end-of-month spend, rank what's driving the burn rate, warn "
    "before you breach a monthly cap, and (pro tier) hand you concrete model-"
    "routing + caching + compaction recommendations with projected $ savings. "
    "Companion to milo-cost-auditor: cost-auditor diagnoses past waste, this "
    "one predicts future spend."
)


def build_server() -> FastMCP:
    """Construct + register tools. Public for tests."""
    mcp = FastMCP(name=SERVER_NAME, instructions=SERVER_INSTRUCTIONS)

    @mcp.tool(
        name="forecast_monthly_spend",
        description=(
            "Free tier. Project this month's end-of-month LLM spend from your "
            "local logs. Reads Claude Code JSONL session logs by default "
            "(`~/.claude/projects/*/*.jsonl`); pass `log_path` to point at a "
            "different file or directory. Returns projected EOM spend with a "
            "lo/mid/hi confidence band, 7-day rolling avg, day-of-week "
            "seasonality applied to remaining days, and a plain-English note."
        ),
    )
    def forecast_monthly_spend(
        log_path: Optional[str] = None,
        current_month_days_so_far: Optional[int] = None,
    ) -> Dict[str, Any]:
        return tools.forecast_monthly_spend_tool(
            log_path=log_path,
            current_month_days_so_far=current_month_days_so_far,
        )

    @mcp.tool(
        name="identify_spike_drivers",
        description=(
            "Free tier. Rank the top 5 subagents / projects / files driving the "
            "current spend curve, comparing the last third of the lookback "
            "window (recent) vs the prior two thirds (trailing). Returns each "
            "driver's per-day cost delta, percent change, and a plain-English "
            "rationale. Use this after forecast_monthly_spend tells you you're "
            "trending hot."
        ),
    )
    def identify_spike_drivers(
        log_path: Optional[str] = None,
        lookback_days: int = 7,
    ) -> Dict[str, Any]:
        return tools.identify_spike_drivers_tool(
            log_path=log_path,
            lookback_days=lookback_days,
        )

    @mcp.tool(
        name="budget_alert_check",
        description=(
            "Free tier (3 calls/day cap). Given a monthly USD cap, return "
            "hours_until_breach + breach_risk_pct + level (clear|warn|urgent|"
            "breached). Combines the rolling spend rate with the forecast "
            "confidence band; the LO/HI of the band drives the risk percentage."
        ),
    )
    def budget_alert_check(
        monthly_cap_usd: float,
        log_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        return tools.budget_alert_check_tool(
            monthly_cap_usd=monthly_cap_usd,
            log_path=log_path,
        )

    @mcp.tool(
        name="optimize_recommendations",
        description=(
            "Paid tier ($19/mo). Validates the pro_key locally (HMAC-signed "
            "token from the storefront) and returns model-routing, caching, "
            "and compaction recommendations with projected $/mo savings from "
            "your real usage data. Without a valid pro_key, returns 1 generic "
            "tip + an x402-style payment_request envelope with the PayPal URL."
        ),
    )
    def optimize_recommendations(
        log_path: Optional[str] = None,
        pro_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return tools.optimize_recommendations_tool(
            log_path=log_path,
            pro_key=pro_key,
        )

    return mcp


def emit_boot_banner() -> None:
    """First-line banner on startup. Goes to stderr so it never pollutes the MCP stream."""
    install = telemetry.install_id()
    banner = telemetry.first_invocation_banner() or ""
    msg = (
        f"# milo-usage-forecaster v{__version__}\n"
        f"# install_id={install}\n"
        f"# telemetry: local-only SQLite at {telemetry.ensure_home() / telemetry.DB_NAME}\n"
    )
    if banner:
        msg += banner + "\n"
    if payment.is_dev_mode():
        msg += (
            "# WARNING: MILO_USAGE_FORECASTER_HMAC_KEY not set. "
            "Running in dev mode — pro_keys signed with the dev secret will validate, "
            "but production keys won't. Set MILO_USAGE_FORECASTER_HMAC_KEY for production.\n"
        )
    sys.stderr.write(msg)
    sys.stderr.flush()


def main() -> int:
    """CLI entry point. Blocks on stdio MCP server."""
    emit_boot_banner()
    mcp = build_server()
    # FastMCP exposes a synchronous .run() that handles asyncio for us.
    mcp.run("stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by __main__.py
    sys.exit(main())
