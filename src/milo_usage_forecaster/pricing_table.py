"""
LLM pricing table — frozen at 2026-05 list prices.

Units: USD per 1,000,000 input tokens / USD per 1,000,000 output tokens.
Source: each provider's public pricing page as of May 2026.

This is a verbatim copy of the milo-cost-auditor pricing table so that the
forecaster's projections match the auditor's diagnosis when the two packages
sit side-by-side. Keep them in sync when refreshing prices.

Quality bands are my (Milo's) opinion based on shipped task-completion rates
across my own routing layer. Treat them as a starting point, not a benchmark.
Quality band scale:
  5 = frontier (Opus, GPT-5, Gemini 3 Ultra-class)
  4 = strong (Sonnet, GPT-4o-class)
  3 = capable (Haiku, gpt-4o-mini, Llama-3.3-70b)
  2 = small-task (Groq Llama-3.1-8b, gpt-3.5-class)
  1 = embedding/cheap-throughput

If you spot a stale price, file an issue at
https://github.com/miloantaeus/milo-usage-forecaster/issues — I'll refresh.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ModelPrice:
    """One row of the pricing table."""

    provider: str
    model: str
    input_per_million: float   # USD per 1M input tokens
    output_per_million: float  # USD per 1M output tokens
    context_window: int
    quality_band: int          # 1..5
    notes: str = ""

    def cost_for(self, input_tokens: int, output_tokens: int) -> float:
        """Cost in USD for a given call shape."""
        return (
            (input_tokens / 1_000_000.0) * self.input_per_million
            + (output_tokens / 1_000_000.0) * self.output_per_million
        )


# Frozen snapshot — 2026-05-16. Mirrors milo-cost-auditor pricing table.
PRICING_TABLE: List[ModelPrice] = [
    # ---- OpenAI (current API pricing, 2026-05) ----
    ModelPrice("openai", "gpt-5.5",             5.00, 30.00, 400_000, 5, "Current flagship"),
    ModelPrice("openai", "gpt-5.4-mini",        0.75,  4.50, 256_000, 4, "Default mini reasoning model"),
    ModelPrice("openai", "gpt-5.4-nano",        0.20,  1.25, 128_000, 3, "Cheap+fast routing/classify"),
    ModelPrice("openai", "gpt-4o",              2.50, 10.00, 128_000, 4, "LEGACY flagship — usually overspend; migrate to gpt-5.4-mini"),
    ModelPrice("openai", "gpt-4o-mini",         0.15,  0.60, 128_000, 3, "LEGACY mini, scheduled for deprecation"),
    ModelPrice("openai", "o3",                  2.00,  8.00, 200_000, 5, "Current frontier reasoning"),
    ModelPrice("openai", "o3-mini",             1.10,  4.40, 200_000, 4, "Cheaper reasoning"),

    # ---- Anthropic (claude.com/api 2026-05) ----
    ModelPrice("anthropic", "claude-opus-4.7",     5.00, 25.00, 200_000, 5, "Current frontier — high-stakes reasoning"),
    ModelPrice("anthropic", "claude-sonnet-4.6",   3.00, 15.00, 200_000, 4, "Default for serious coding/writing"),
    ModelPrice("anthropic", "claude-haiku-4.5",    1.00,  5.00, 200_000, 3, "Cheap and fast; routine tasks"),
    ModelPrice("anthropic", "claude-3-opus",      15.00, 75.00, 200_000, 5, "LEGACY frontier — Opus 4.7 usually better+cheaper"),
    ModelPrice("anthropic", "claude-3-sonnet",     3.00, 15.00, 200_000, 4, "LEGACY Sonnet"),
    ModelPrice("anthropic", "claude-3-haiku",      0.25,  1.25, 200_000, 2, "LEGACY (Bedrock/Vertex only)"),

    # ---- Google (ai.google.dev/pricing 2026-05) ----
    ModelPrice("google", "gemini-3.1-pro-preview",  2.00, 12.00, 2_000_000, 5, "Current frontier; <=200k input pricing"),
    ModelPrice("google", "gemini-3-flash-preview",  0.50,  3.00, 1_000_000, 3, "Default cheap option, very fast"),
    ModelPrice("google", "gemini-2.5-pro",          1.25, 10.00, 2_000_000, 4, "LEGACY pro"),
    ModelPrice("google", "gemini-2.5-flash",        0.075, 0.30, 1_000_000, 3, "LEGACY flash, still very cheap"),

    # ---- MiniMax (api.minimax.io 2026-05) ----
    ModelPrice("minimax", "minimax-m2.7",            0.30, 1.20, 256_000, 4, "Strong reasoning, Anthropic-compatible API"),
    ModelPrice("minimax", "minimax-m2.7-highspeed",  0.30, 1.20, 256_000, 4, "Same model, larger free quota (15k req/5hr Starter)"),
    ModelPrice("minimax", "abab-7-chat",             0.20, 0.80, 245_000, 3, "Legacy chat"),

    # ---- Groq (groq.com/pricing 2026-05) ----
    ModelPrice("groq", "llama-3.3-70b-versatile",   0.59, 0.79, 131_000, 3, "Fast OSS, great for routing"),
    ModelPrice("groq", "llama-3.1-8b-instant",      0.05, 0.08, 131_000, 2, "Cheapest fast inference"),

    # ---- OpenRouter (broker; quotes are mid-2026 averages, varies by route) ----
    ModelPrice("openrouter", "openrouter/auto",     1.00, 4.00, 128_000, 3, "Aggregator; price depends on selected route"),

    # ---- DeepSeek (api-docs.deepseek.com 2026-05) ----
    ModelPrice("deepseek", "deepseek-v3",           0.14, 0.28, 128_000, 4, "V3 base — strong coder, MIT weights"),
    ModelPrice("deepseek", "deepseek-chat",         0.27, 1.10, 128_000, 4, "= V4-Flash; legacy alias retires 2026-07-24"),
    ModelPrice("deepseek", "deepseek-reasoner",     0.55, 2.19, 128_000, 4, "= V4-Flash thinking; legacy alias retires 2026-07-24"),

    # ---- Cerebras (flagged for re-verify) ----
    ModelPrice("cerebras", "llama-3.3-70b",         0.60, 0.85, 128_000, 3, "Ultra-fast wafer-scale (UNVERIFIED 2026-05)"),
    ModelPrice("cerebras", "llama-3.1-8b",          0.10, 0.15, 128_000, 2, "Cheapest+fastest small model (UNVERIFIED 2026-05)"),
]


# Cached input is much cheaper than fresh input — Anthropic prompt-caching
# charges ~10% of input price for cache reads. We use 0.10 as a generic
# multiplier; per-provider precision arrives in v0.2.
CACHE_READ_MULTIPLIER = 0.10
# Anthropic charges 1.25x input price for writing to a 5-min ephemeral cache.
CACHE_CREATION_MULTIPLIER = 1.25


# ------------- lookup helpers ----------------------------------------------

def _normalize(name: str) -> str:
    """Lowercase + strip provider prefix + collapse separators."""
    s = name.lower().strip()
    if "/" in s:
        s = s.split("/", 1)[1]
    s = s.replace("_", "-")
    return s


def lookup(model: str) -> Optional[ModelPrice]:
    """Look up a model by name (case-insensitive, accepts provider/model form).

    Also tolerates Claude Code's dash-suffix form like 'claude-opus-4-7' which
    maps to 'claude-opus-4.7' in our table.
    """
    needle = _normalize(model)
    for entry in PRICING_TABLE:
        if _normalize(entry.model) == needle:
            return entry
    # Claude Code logs use dash-suffix versioning ('claude-opus-4-7' for 4.7).
    # Try swapping the last 2 dashes back to dots and retrying.
    parts = needle.rsplit("-", 2)
    if len(parts) == 3 and parts[-1].isdigit() and parts[-2].isdigit():
        dotted = f"{parts[0]}-{parts[1]}.{parts[2]}"
        for entry in PRICING_TABLE:
            if _normalize(entry.model) == dotted:
                return entry
    # second pass: prefix match (e.g. "gpt-4o-2024-08-06" -> "gpt-4o")
    for entry in PRICING_TABLE:
        norm = _normalize(entry.model)
        if needle.startswith(norm) or norm.startswith(needle):
            return entry
    return None


def by_provider(provider: str) -> List[ModelPrice]:
    """All models for a given provider."""
    return [m for m in PRICING_TABLE if m.provider == provider.lower()]


def cheaper_than(model: ModelPrice, min_quality: int = 0) -> List[ModelPrice]:
    """Return all models cheaper than the reference, ranked by avg cost ascending,
    optionally filtered by minimum quality band."""
    ref_avg = (model.input_per_million + model.output_per_million) / 2.0
    out: List[ModelPrice] = []
    for entry in PRICING_TABLE:
        if entry.model == model.model:
            continue
        if entry.quality_band < min_quality:
            continue
        avg = (entry.input_per_million + entry.output_per_million) / 2.0
        if avg < ref_avg:
            out.append(entry)
    out.sort(key=lambda m: (m.input_per_million + m.output_per_million) / 2.0)
    return out


# Default per-task-pattern quality requirements (used by optimization_recommender).
TASK_QUALITY_REQUIREMENT: Dict[str, int] = {
    "code-completion":     3,
    "summarization":       3,
    "routine-synthesis":   3,
    "deep-reasoning":      4,
    "creative-writing":    4,
    "extraction":          2,
    "classification":      2,
    "translation":         3,
    "agent-loop":          3,
}
