"""
Milo Usage Forecaster — MCP server that predicts your LLM spend before you breach budget.

I'm Milo Antaeus. After shipping milo-cost-auditor (which tells you where past
spend went wrong), the next pain point I kept hearing from devs was: "great,
but how do I see the spike coming before it happens?" This server is the
prediction half of that pair.

Default input is your local Claude Code project logs at `~/.claude/projects/*.jsonl`,
which capture model + token usage on every assistant turn. Cursor + Codex CLI
log shapes are supported via the same log_parser with format auto-detection.

Free tier:
  - forecast_monthly_spend:  rolling 7-day avg + day-of-week seasonality projection
  - identify_spike_drivers:  top-5 subagents/projects/files driving the burn rate
  - budget_alert_check:      hours-until-breach + risk band (3 calls/day cap)

Pro tier ($19/mo via PayPal storefront):
  - optimize_recommendations: model routing + caching + compaction suggestions
                              with projected $ savings

License: MIT
Homepage: https://github.com/miloantaeus/milo-usage-forecaster
Companion: https://github.com/miloantaeus/milo-cost-auditor
"""

__version__ = "0.1.0"
__author__ = "Milo Antaeus"
__email__ = "miloantaeus@gmail.com"
__license__ = "MIT"

# REVENUE-MCP-USAGE-FORECASTER-V0.1-20260517
# Ship marker: this product is Milo Antaeus's second revenue MCP server.
# Companion to milo-cost-auditor. Kill criterion: see README.


from milo_usage_forecaster import (
    budget_alert,
    forecast_engine,
    log_parser,
    optimization_recommender,
    payment,
    pricing_table,
    spike_detector,
    telemetry,
)

__all__ = [
    "__version__",
    "budget_alert",
    "forecast_engine",
    "log_parser",
    "optimization_recommender",
    "payment",
    "pricing_table",
    "spike_detector",
    "telemetry",
]
