"""Shared pytest fixtures + path bootstrap.

Per the cost-auditor pattern, we run tests in DEV_MODE so HMAC payment
validation uses the per-process random dev key. Production refuses dev
fallback unless MILO_USAGE_FORECASTER_DEV_MODE=1 is explicitly set.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Make src/ importable without install.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def isolate_state_and_dev_mode(tmp_path, monkeypatch):
    """Send all telemetry + log-discovery state into a per-test tmp dir."""
    home = tmp_path / "milo-usage-forecaster-home"
    monkeypatch.setenv("MILO_USAGE_FORECASTER_HOME", str(home))
    # SECURITY: tests opt into dev mode explicitly (inherited from cost-auditor v0.1.3).
    monkeypatch.setenv("MILO_USAGE_FORECASTER_DEV_MODE", "1")
    # Point the default log-discovery root at an empty tmp dir so tests don't
    # accidentally read the user's real ~/.claude/projects.
    log_root = tmp_path / "claude-projects"
    log_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MILO_USAGE_FORECASTER_LOG_ROOT", str(log_root))
    # Wipe in-process flags
    from milo_usage_forecaster import telemetry
    telemetry.reset_for_tests()
    yield


@pytest.fixture
def static_claude_code_log_path() -> Path:
    """Path to the static fixture file (for log-parser shape tests)."""
    return HERE / "fixtures" / "sample_claude_code_log.jsonl"


@pytest.fixture
def static_cursor_log_path() -> Path:
    return HERE / "fixtures" / "sample_cursor_log.json"


def _build_event(
    *,
    days_ago: float,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation: int = 0,
    cache_read: int = 0,
    session_id: str = "session-X",
    subagent_text: str = "",
) -> Dict[str, Any]:
    """Make one Claude-Code-shape JSONL row whose timestamp is `days_ago` days from now."""
    ts = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    content_text = (
        f'<scheduled-task name="{subagent_text}" file="x">noop</scheduled-task>'
        if subagent_text else "reply"
    )
    return {
        "type": "assistant",
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "sessionId": session_id,
        "parentUuid": f"uuid-{days_ago}",
        "message": {
            "model": model,
            "id": f"msg_{int(days_ago * 1000)}",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": content_text}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
                "output_tokens": output_tokens,
            },
        },
    }


@pytest.fixture
def synthetic_log_dir(tmp_path) -> Path:
    """Build a synthetic Claude Code project tree with 30d of realistic usage.

    Layout mirrors `~/.claude/projects/<project>/<session>.jsonl`. Two projects,
    two subagents, one spike on day 1 (yesterday) for spike-detection tests.
    """
    base = tmp_path / "claude-projects"
    base.mkdir(parents=True, exist_ok=True)
    project_a = base / "-Users-test-projectA"
    project_b = base / "-Users-test-projectB"
    project_a.mkdir()
    project_b.mkdir()

    # Project A: steady opus usage over 30 days (~1 event/day) + a spike on day 1.
    rows_a: List[Dict[str, Any]] = []
    for d in range(30, 0, -1):
        rows_a.append(_build_event(
            days_ago=d,
            model="claude-opus-4-7",
            input_tokens=300, output_tokens=900,
            cache_creation=1000, cache_read=4000,
            session_id="session-A",
            subagent_text="milo-hourly-supervision",
        ))
    # Spike yesterday (day=1): 10 expensive calls
    for i in range(10):
        rows_a.append(_build_event(
            days_ago=1 - (i / 100.0),  # spread within yesterday
            model="claude-opus-4-7",
            input_tokens=80_000, output_tokens=2_000,
            session_id="session-A-spike",
            subagent_text="runaway-research-loop",
        ))
    session_path_a = project_a / "session-A.jsonl"
    with session_path_a.open("w", encoding="utf-8") as f:
        for row in rows_a:
            f.write(json.dumps(row) + "\n")

    # Project B: gentle sonnet usage with no spike.
    rows_b: List[Dict[str, Any]] = []
    for d in range(30, 0, -1):
        rows_b.append(_build_event(
            days_ago=d,
            model="claude-sonnet-4-6",
            input_tokens=5_000, output_tokens=600,
            cache_creation=0, cache_read=500,
            session_id="session-B",
            subagent_text="main",
        ))
    session_path_b = project_b / "session-B.jsonl"
    with session_path_b.open("w", encoding="utf-8") as f:
        for row in rows_b:
            f.write(json.dumps(row) + "\n")

    return base


@pytest.fixture
def synthetic_log_root_set(synthetic_log_dir, monkeypatch) -> Path:
    """Same as synthetic_log_dir but also points the default log-root env at it."""
    monkeypatch.setenv("MILO_USAGE_FORECASTER_LOG_ROOT", str(synthetic_log_dir))
    return synthetic_log_dir
