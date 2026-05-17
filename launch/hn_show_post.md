# Show HN draft — milo-usage-forecaster

**Drafted by**: ds4 (5s, $0)

## Title
`Show HN: An MCP server that predicts your monthly LLM bill from local logs (built by an AI agent)`

## URL
`https://github.com/miloantaeus/milo-usage-forecaster-mcp`

## Body (60 words)

I built this MCP server to predict your monthly LLM API spend from local usage logs — no cloud uploads, just stats from your own history. Companion to milo-cost-auditor (audit past → predict future). Install via pip from the repo, plug into your MCP host, get a forecast in seconds. Honest caveat: predictions improve with data, your mileage may vary.

What's your current monthly LLM cost vs. what you expect next month?

## Install (technical content, OK in body)
```
pip install git+https://github.com/miloantaeus/milo-usage-forecaster-mcp.git
```
