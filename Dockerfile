# Dockerfile for milo-usage-forecaster MCP server
# Required by Glama.ai introspection checks (per awesome-mcp-servers PR pattern).
# Builds a minimal Python 3.13 image, installs the package, exposes the MCP
# server on stdio (the canonical MCP transport).
#
# Build:  docker build -t milo-usage-forecaster .
# Run:    docker run -i milo-usage-forecaster   # stdio mode — feed JSON-RPC via stdin
# Test:   echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"docker-smoke","version":"0.0.0"}}}' | docker run -i milo-usage-forecaster

FROM python:3.13-slim

LABEL org.opencontainers.image.title="milo-usage-forecaster"
LABEL org.opencontainers.image.description="MCP server that forecasts LLM spend, ranks spike drivers, and warns before budget breach. Companion to milo-cost-auditor."
LABEL org.opencontainers.image.source="https://github.com/miloantaeus/milo-usage-forecaster"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.authors="Milo Antaeus <miloantaeus@gmail.com>"

# Don't write .pyc files; keep image small + reproducible
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install only the deps first (cache layer) — copy pyproject only
COPY pyproject.toml ./

# Install the package + its runtime deps
COPY src /app/src
RUN pip install --no-cache-dir .

# MCP servers communicate over stdio. The console-script entry exposes `mcp-usage-forecaster`.
# Glama's introspection should be able to send an `initialize` JSON-RPC frame and get
# back our serverInfo (name=milo-usage-forecaster, version=0.1.x) + tool list.
CMD ["python", "-m", "milo_usage_forecaster"]
