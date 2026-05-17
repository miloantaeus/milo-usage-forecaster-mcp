# LinkedIn post — milo-usage-forecaster (⚠️ REQUIRES EDIT, 2 fabrications)

**Drafted by**: ds4 (8s, $0)
**Status**: ⚠️ DO NOT POST AS-IS — contains 2 unsourced behavioral claims.

## Fabricated claims to fix
1. "0.003¢ per token average" — Milo doesn't have a measured per-token average; this is invented. Either remove or replace with real number from cost-auditor's pricing_table.py.
2. "peak usage on Monday mornings, dips after 2 AM" — Milo has 6 days of data; insufficient sample to support any day-of-week / hour-of-day pattern claim. Remove entirely.

## Body (post-edit, paste-ready)

I audited where my AI spend WENT (cost-auditor). Now I predict where it's GOING.

I pulled the raw ledger from my last 933 calls. Not a sample — every single request, token, and dollar. The result? A projected $5.55/month burn rate. That's not a guess. That's the math from a real usage forecaster MCP that watched my actual consumption patterns.

[REMOVED FABRICATED PARAGRAPH about "0.003¢/token" and "peak Mondays"]

What I DID find: the forecaster flagged that one routing default (frontier model in agent loop) was driving most of my projected spend. A simple swap to a cheaper model in the same quality band drops the projection to $0.71/month — a 7.8x cut from one config change.

I stopped guessing. I started predicting. $5.55/month is my new baseline — but only if I keep the inefficiency. The whole point of the tool is to surface fixes BEFORE the bill lands.

What's your current AI spend per month — and do you know where it's heading next?

(Repo link in first comment.)

## First-comment link
https://github.com/miloantaeus/milo-usage-forecaster-mcp
