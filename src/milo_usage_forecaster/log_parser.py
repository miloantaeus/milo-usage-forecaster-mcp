"""
Local-log parser for Claude Code / Cursor / Codex CLI usage history.

Default discovery: walk `~/.claude/projects/*/*.jsonl` (and individual .jsonl
files inside `~/.claude/projects/`). Each Claude Code JSONL line is one event;
the lines we care about have `type == "assistant"` (or no type but a populated
`message.usage` block) and carry token counts.

Schema we extract per usage event:
    timestamp    -- ISO-8601 UTC
    model        -- e.g. 'claude-opus-4-7' (we normalize via pricing_table.lookup)
    project      -- top-level project dir name (e.g. '-Users-miloantaeus')
    file         -- session file path (used as a per-conversation key)
    input_tokens
    cache_creation_input_tokens
    cache_read_input_tokens
    output_tokens
    subagent     -- best-effort guess from session metadata or skill tags;
                    falls back to 'main'

No external API calls. Pure file I/O + JSON. Reads only.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple


# Limit how much we'll read from any one file at parse time. Keeps a runaway
# multi-GB log from OOM-ing the MCP server.
MAX_BYTES_PER_FILE = 200 * 1024 * 1024  # 200 MB


@dataclass
class UsageEvent:
    """Normalized internal record for one assistant turn."""

    timestamp: str
    model: str
    project: str
    file: str
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0
    subagent: str = "main"
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_input_tokens(self) -> int:
        """Total billable input including cache creation + cache reads."""
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


# ---- discovery ------------------------------------------------------------


def default_log_root() -> Path:
    """Where Claude Code keeps its per-project session logs."""
    custom = os.environ.get("MILO_USAGE_FORECASTER_LOG_ROOT")
    return Path(custom) if custom else Path.home() / ".claude" / "projects"


def discover_logs(log_path: Optional[str] = None) -> List[Path]:
    """Discover JSONL log files. log_path can be a file, a dir, or None (default root)."""
    if log_path:
        p = Path(log_path).expanduser()
        if p.is_file():
            return [p]
        if p.is_dir():
            return _walk_dir(p)
        # Glob pattern fallback
        parent = p.parent if p.parent.exists() else Path.cwd()
        return sorted(parent.glob(p.name))
    root = default_log_root()
    if not root.exists():
        return []
    return _walk_dir(root)


def _walk_dir(d: Path) -> List[Path]:
    """Return all .jsonl + .json files under d (recursive, sorted, deduped)."""
    out: List[Path] = []
    for ext in ("*.jsonl", "*.json"):
        out.extend(d.rglob(ext))
    # Dedup + sort for determinism
    return sorted({p.resolve() for p in out})


# ---- parsing --------------------------------------------------------------


_ASSISTANT_TYPE = "assistant"


def iter_events(log_paths: Iterable[Path]) -> Iterator[UsageEvent]:
    """Stream usage events across the given files. Skips unreadable rows."""
    for path in log_paths:
        try:
            yield from _iter_file_events(path)
        except (OSError, PermissionError):
            # Quietly skip unreadable files — don't crash the whole audit.
            continue


def _iter_file_events(path: Path) -> Iterator[UsageEvent]:
    """Yield usage events from one log file."""
    project = _project_from_path(path)
    file_str = str(path)
    size = path.stat().st_size if path.exists() else 0
    if size > MAX_BYTES_PER_FILE:
        # Skip pathologically large files; v0.2 can chunk.
        return
    # Cursor's "single JSON array" format vs Claude Code's "one JSON per line".
    suffix = path.suffix.lower()
    if suffix == ".json":
        yield from _iter_cursor_json(path, project, file_str)
        return
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ev = _row_to_event(row, project, file_str)
            if ev is not None:
                yield ev


def _iter_cursor_json(path: Path, project: str, file_str: str) -> Iterator[UsageEvent]:
    """Cursor-style log: a JSON array of call records (one shot, not line-by-line)."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    rows: List[Dict[str, Any]]
    if isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
    elif isinstance(data, dict):
        # Cursor sometimes wraps: {"calls": [...]}
        inner = data.get("calls") or data.get("events") or data.get("usage")
        if isinstance(inner, list):
            rows = [r for r in inner if isinstance(r, dict)]
        else:
            rows = [data]
    else:
        return
    for row in rows:
        ev = _row_to_event(row, project, file_str)
        if ev is not None:
            yield ev


def _row_to_event(row: Dict[str, Any], project: str, file_str: str) -> Optional[UsageEvent]:
    """Extract a UsageEvent from one row. Returns None if it isn't a token-bearing row."""
    # Claude Code: top-level type "assistant" + message.usage block
    row_type = row.get("type")
    # Two shapes we accept:
    #   shape A — Claude Code: {type:"assistant", message:{model,usage:{...}}, timestamp}
    #   shape B — Cursor / Codex: {timestamp, model, input_tokens, output_tokens, ...}
    msg = row.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
        # Shape A
        if row_type and row_type != _ASSISTANT_TYPE:
            return None
        u = msg["usage"]
        model = str(msg.get("model") or row.get("model") or "unknown").strip()
        if model == "<synthetic>" or not model:
            return None
        ts = _normalize_ts(row.get("timestamp") or msg.get("timestamp"))
        if ts is None:
            return None
        subagent = _subagent_guess(row)
        return UsageEvent(
            timestamp=ts,
            model=model,
            project=project,
            file=file_str,
            input_tokens=_to_int(u.get("input_tokens", 0)),
            cache_creation_input_tokens=_to_int(u.get("cache_creation_input_tokens", 0)),
            cache_read_input_tokens=_to_int(u.get("cache_read_input_tokens", 0)),
            output_tokens=_to_int(u.get("output_tokens", 0)),
            subagent=subagent,
            raw=row,
        )
    # Shape B
    input_tokens_keys = (
        "input_tokens", "prompt_tokens", "tokens_in", "n_context_tokens_total"
    )
    output_tokens_keys = (
        "output_tokens", "completion_tokens", "tokens_out", "n_generated_tokens_total"
    )
    model_keys = ("model", "model_name", "engine", "model_id")
    ts_keys = ("timestamp", "ts", "created_at", "request_time", "date")

    def first(keys: Tuple[str, ...], default: Any = None) -> Any:
        for k in keys:
            if k in row and row[k] not in (None, "", "null"):
                return row[k]
        return default

    in_tok = _to_int(first(input_tokens_keys, 0))
    out_tok = _to_int(first(output_tokens_keys, 0))
    if in_tok == 0 and out_tok == 0:
        return None
    model = str(first(model_keys, "unknown")).strip()
    if not model or model == "unknown":
        return None
    ts = _normalize_ts(first(ts_keys))
    if ts is None:
        return None
    return UsageEvent(
        timestamp=ts,
        model=model,
        project=project,
        file=file_str,
        input_tokens=in_tok,
        cache_creation_input_tokens=_to_int(row.get("cache_creation_input_tokens", 0)),
        cache_read_input_tokens=_to_int(row.get("cache_read_input_tokens", 0)),
        output_tokens=out_tok,
        subagent=str(row.get("subagent") or row.get("agent") or "main"),
        raw=row,
    )


# ---- helpers --------------------------------------------------------------


def _project_from_path(path: Path) -> str:
    """Guess the project name from a Claude Code log path.

    Layout is `~/.claude/projects/<project>/<session>.jsonl` so the parent dir name
    is the encoded project path. If the layout doesn't match we return the
    nearest meaningful directory.
    """
    root = default_log_root()
    try:
        rel = path.resolve().relative_to(root.resolve())
        # Top component of rel is the project bucket
        return rel.parts[0] if rel.parts else path.parent.name
    except ValueError:
        return path.parent.name


_SUBAGENT_RE = re.compile(r"<scheduled-task name=\"([^\"]+)\"", re.IGNORECASE)


def _subagent_guess(row: Dict[str, Any]) -> str:
    """Best-effort subagent label.

    Claude Code scheduled-task runs and SDK agent dispatches don't always carry
    an explicit subagent field, but they often include a `<scheduled-task name="...">`
    tag in the first content block. We sniff that for a friendlier label.
    """
    content = None
    msg = row.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            text = first.get("text") or first.get("thinking") or ""
            if isinstance(text, str):
                m = _SUBAGENT_RE.search(text[:512])
                if m:
                    return m.group(1)
    # Fall back to explicit fields if present
    for key in ("subagent", "agent", "agent_id"):
        v = row.get(key)
        if v:
            return str(v)
    # `sessionId` is conversation-scoped; not a great agent label but stable.
    return "main"


def _to_int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _normalize_ts(ts: Any) -> Optional[str]:
    """Coerce a timestamp to ISO-8601 UTC string. Returns None if unparseable."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        try:
            dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(ts, str):
        return None
    s = ts.strip()
    if not s:
        return None
    # Already ISO?
    try:
        s2 = s[:-1] + "+00:00" if s.endswith("Z") else s
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


# ---- aggregation primitives ----------------------------------------------


def collect_events(log_path: Optional[str] = None) -> List[UsageEvent]:
    """Discover + parse all events. Convenience wrapper for tests and tools."""
    paths = discover_logs(log_path)
    return list(iter_events(paths))


def events_in_window(events: List[UsageEvent], days: int) -> List[UsageEvent]:
    """Return events whose timestamp is within the last `days` days from now (UTC)."""
    if days <= 0 or not events:
        return list(events)
    now = datetime.now(tz=timezone.utc)
    cutoff = now.timestamp() - days * 86400.0
    out: List[UsageEvent] = []
    for ev in events:
        try:
            ts = datetime.fromisoformat(ev.timestamp.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            out.append(ev)
    return out


def event_to_cost_usd(event: UsageEvent) -> float:
    """Cost in USD for one event, applying cache multipliers when present."""
    from milo_usage_forecaster.pricing_table import (
        CACHE_CREATION_MULTIPLIER,
        CACHE_READ_MULTIPLIER,
        lookup,
    )
    price = lookup(event.model)
    if price is None:
        return 0.0
    fresh_in = (event.input_tokens / 1_000_000.0) * price.input_per_million
    cache_create = (
        (event.cache_creation_input_tokens / 1_000_000.0)
        * price.input_per_million
        * CACHE_CREATION_MULTIPLIER
    )
    cache_read = (
        (event.cache_read_input_tokens / 1_000_000.0)
        * price.input_per_million
        * CACHE_READ_MULTIPLIER
    )
    output = (event.output_tokens / 1_000_000.0) * price.output_per_million
    return fresh_in + cache_create + cache_read + output


def group_by_day(events: List[UsageEvent]) -> Dict[str, List[UsageEvent]]:
    """Bucket events by YYYY-MM-DD (UTC). Returns insertion-ordered dict."""
    out: Dict[str, List[UsageEvent]] = {}
    for ev in events:
        try:
            dt = datetime.fromisoformat(ev.timestamp.replace("Z", "+00:00"))
            day = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        out.setdefault(day, []).append(ev)
    return out
