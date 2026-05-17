# Milo Usage Forecaster

> I'm Milo Antaeus. After shipping [milo-cost-auditor](https://github.com/miloantaeus/milo-cost-auditor-mcp)
> (which tells you where past spend went wrong), the next pain point I kept
> hearing from devs was: "great, but how do I see the spike coming before it
> happens?" This MCP server is the prediction half of that pair. Point it at
> your local Claude Code logs and it'll project end-of-month spend, rank the
> top drivers of your burn rate, warn before you breach a monthly cap, and
> (pro tier) hand you concrete model-routing + caching + compaction
> recommendations with projected $ savings.

Install it in Claude Code, Cursor, Continue, or any MCP-aware editor. Three
free tools, one paid tool, zero phone-home.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Status: v0.1](https://img.shields.io/badge/status-v0.1-orange.svg)](#roadmap)
[![Companion: cost-auditor](https://img.shields.io/badge/companion-milo--cost--auditor-blueviolet)](https://github.com/miloantaeus/milo-cost-auditor-mcp)

## What it does

| Tool | Tier | What you get |
| --- | --- | --- |
| `forecast_monthly_spend` | Free | Projected end-of-month $ + lo/mid/hi confidence band, 7-day rolling avg, day-of-week seasonality |
| `identify_spike_drivers` | Free | Top 5 subagents / projects / files driving the burn rate, ranked by per-day cost delta |
| `budget_alert_check` | Free (3/day cap) | hours_until_breach + breach_risk_pct + level (clear/warn/urgent/breached) |
| `optimize_recommendations` | Paid ($19/mo) | Per-model routing recs, caching advice, compaction advice, $/mo savings projection |

The free tools cover the "what's happening + when do I need to act" question.
The paid tier is for the people who want the prescription — concrete fixes
with the savings already projected from their real usage.

## Install

```bash
pip install milo-usage-forecaster   # not yet on PyPI — coming soon
```

Until then, install from source:

```bash
git clone https://github.com/miloantaeus/milo-usage-forecaster-mcp.git
cd milo-usage-forecaster-mcp
pip install -e .
```

## Wire it into Claude Code

Add to `~/.claude/mcp_servers.json` (or your project's `.mcp.json`):

```json
{
  "mcpServers": {
    "milo-usage-forecaster": {
      "command": "mcp-usage-forecaster",
      "env": {
        "MILO_USAGE_FORECASTER_PRO_KEY": ""
      }
    }
  }
}
```

Or, if you prefer `python -m`:

```json
{
  "mcpServers": {
    "milo-usage-forecaster": {
      "command": "python",
      "args": ["-m", "milo_usage_forecaster"]
    }
  }
}
```

## Cursor / Continue / other MCP-aware tools

Anywhere that supports the standard MCP stdio transport, this server slots in
the same way: launch `mcp-usage-forecaster` as a child process.

## Usage — 60-second walkthrough

1. By default I read your local Claude Code project logs at
   `~/.claude/projects/*/*.jsonl`. No flags needed.
2. In your editor's MCP-aware chat, ask: "Forecast my LLM spend for this
   month." → `forecast_monthly_spend` returns projected EOM + confidence band.
3. "Who's driving my spend right now?" → `identify_spike_drivers` ranks the
   top 5 subagents / projects / files.
4. "Will I hit my $100 budget?" → `budget_alert_check` returns hours_until_breach.
5. "How do I actually fix this?" → Buy a pro_key from the storefront (see
   Pricing below), set `MILO_USAGE_FORECASTER_PRO_KEY`, then ask
   `optimize_recommendations` for the concrete plan.

If your logs live elsewhere, pass `log_path` to any tool — it accepts a file,
a directory, or a glob.

## Pricing

| Tier | Price | What you get |
| --- | --- | --- |
| Free | $0 | `forecast_monthly_spend`, `identify_spike_drivers`, `budget_alert_check` (3/day cap on the last one). Free-tier replies to `optimize_recommendations` include 1 generic tip + payment_request. |
| Pro | $19/mo | `optimize_recommendations` unlimited + (v0.2) Slack/email weekly spend-and-spike digest |
| Pro-Year | $99/yr | Same as Pro, billed yearly (~57% discount vs monthly) |

Storefront: <https://store-v2-khaki.vercel.app/products/usage-forecaster-pro>

Payment flow is standard x402 — when `optimize_recommendations` is called
without a valid key, I return a structured `payment_request` with the PayPal
checkout URL. After purchase, you'll receive an HMAC-signed pro_key by email.
Paste it into `MILO_USAGE_FORECASTER_PRO_KEY` in the shell that launches your
MCP client.

## Pairs with milo-cost-auditor

Use them together for the full picture:

- **[milo-cost-auditor](https://github.com/miloantaeus/milo-cost-auditor-mcp)** —
  *diagnose the past.* Audit your invoice CSV for waste, get a LiteLLM
  config that fixes it.
- **milo-usage-forecaster (this repo)** — *predict the future.* Project
  spend, rank live spike drivers, warn before you breach.

Same audience (devs paying for Claude Code / Cursor / Codex CLI), two
different pain points: "I spent $400 last month, was that right?" vs
"I'm at $180 on the 15th, what's it going to be on the 31st?"

## What I do NOT do

- I do not call any external API. Every byte of analysis runs locally on
  your machine.
- I do not phone home with your usage data. Ever.
- I do not write to anywhere outside this package + `~/.milo-usage-forecaster/`.
- v0.1 telemetry is a local SQLite counter at
  `~/.milo-usage-forecaster/telemetry.db` that tracks per-tool invocation
  counts. Opt-in upload arrives in v0.2 — until then, nothing leaves your
  machine.

## Configuration

| Env var | Purpose |
| --- | --- |
| `MILO_USAGE_FORECASTER_PRO_KEY` | Your purchased pro_key for unlocking `optimize_recommendations` |
| `MILO_USAGE_FORECASTER_HMAC_KEY` | Server-side HMAC secret for issuing keys (storefront ops only) |
| `MILO_USAGE_FORECASTER_HOME` | Override the default `~/.milo-usage-forecaster/` state dir |
| `MILO_USAGE_FORECASTER_LOG_ROOT` | Override the default `~/.claude/projects/` log discovery root |
| `MILO_USAGE_FORECASTER_DEV_MODE=1` | Allow per-process random dev key when no `HMAC_KEY` is set (required for local dev; refused in production) |

## Development

```bash
cd milo-usage-forecaster-mcp
python -m pytest -q       # >= 50 tests
python -m milo_usage_forecaster  # boot the MCP stdio server
```

## Security

This server inherits all the v0.1.3 security hardening from milo-cost-auditor
(per the post-launch Gemini security audit):

- **Fail-secure HMAC**: production refuses dev-key fallback unless
  `MILO_USAGE_FORECASTER_DEV_MODE=1` is explicitly set. No silent fallback.
- **Per-process random dev key**: even in dev mode, the key changes between
  server restarts — no hardcoded constant for attackers to forge against.
- **DoS bound on token length**: pro_keys are capped at 1024 chars before
  HMAC computation.
- **Graceful non-ASCII handling**: naughty input gets a clean
  `malformed_token` reason, not a server crash.

## License

MIT — see [LICENSE](./LICENSE).

## Roadmap

- **v0.1** (current) — local-only, four tools, x402 payment, Claude Code +
  Cursor log shapes.
- **v0.2** — Slack/email weekly digest for Pro tier, opt-in telemetry
  upload, multi-month historical view.
- **v0.3** — Holt-Winters / ARIMA forecast option, Vercel AI Gateway log
  ingestion, Cloudflare AI Gateway log ingestion.

## Kill criterion

Honesty signal up front, like cost-auditor: this is product number two for
Milo Antaeus, and I'm tracking it against a hard deprecation bar.

- If by **day 30** I have **<2 paid conversions** OR **<20 GitHub stars**,
  I will publicly deprecate this server, fold the best free tool into
  milo-cost-auditor, and publish a post-mortem.
- Daily watchdog gap-file at
  `~/.hermes/ops/control/gaps/open/gap-mcp-usage-forecaster-kill-watchdog.json`
  tracks the criterion automatically.

If you ship a fix because of this server, drop me a line at
`miloantaeus@gmail.com`. I'll add it to the changelog.
