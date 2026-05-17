"""
Local SQLite telemetry counter.

v0.1 stores everything locally only — no network calls.
Reporting/opt-in upload arrives in v0.2.

Schema:
  install (install_id TEXT, created_at TEXT)
  invocations (tool TEXT, day TEXT, count INTEGER, PRIMARY KEY(tool, day))
  daily_caps (tool TEXT, day TEXT, count INTEGER, PRIMARY KEY(tool, day))
    -- separate table for rate-limited tools (e.g. budget_alert_check capped at 3/day)
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional

_LOCK = threading.Lock()
_FIRST_INVOCATION_SHOWN = False

DEFAULT_HOME = Path.home() / ".milo-usage-forecaster"
DB_NAME = "telemetry.db"
INSTALL_ID_NAME = "install-id"


def _home_dir() -> Path:
    """Where we keep state. Honors MILO_USAGE_FORECASTER_HOME for tests."""
    custom = os.environ.get("MILO_USAGE_FORECASTER_HOME")
    return Path(custom) if custom else DEFAULT_HOME


def ensure_home() -> Path:
    home = _home_dir()
    home.mkdir(parents=True, exist_ok=True)
    return home


def install_id() -> str:
    """Read or create the install UUID. Persistent across runs."""
    home = ensure_home()
    path = home / INSTALL_ID_NAME
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    new_id = str(uuid.uuid4())
    path.write_text(new_id, encoding="utf-8")
    return new_id


def _db_path() -> Path:
    return ensure_home() / DB_NAME


@contextmanager
def _conn():
    conn = sqlite3.connect(_db_path())
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS install (install_id TEXT PRIMARY KEY, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS invocations ("
        "tool TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY(tool, day))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS daily_caps ("
        "tool TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY(tool, day))"
    )


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def record_invocation(tool: str) -> int:
    """Increment the day's counter for `tool`. Returns the new count."""
    with _LOCK, _conn() as conn:
        _init_schema(conn)
        day = _today()
        conn.execute(
            "INSERT INTO invocations (tool, day, count) VALUES (?, ?, 1) "
            "ON CONFLICT(tool, day) DO UPDATE SET count = count + 1",
            (tool, day),
        )
        cur = conn.execute(
            "SELECT count FROM invocations WHERE tool = ? AND day = ?",
            (tool, day),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def increment_daily_cap(tool: str) -> int:
    """Increment the day's cap counter for `tool`. Returns the new count.

    Used by free-tier tools that have a per-day rate limit (e.g. budget_alert_check).
    Tracked separately from invocations so that a tool returning a rate-limit
    message doesn't double-count.
    """
    with _LOCK, _conn() as conn:
        _init_schema(conn)
        day = _today()
        conn.execute(
            "INSERT INTO daily_caps (tool, day, count) VALUES (?, ?, 1) "
            "ON CONFLICT(tool, day) DO UPDATE SET count = count + 1",
            (tool, day),
        )
        cur = conn.execute(
            "SELECT count FROM daily_caps WHERE tool = ? AND day = ?",
            (tool, day),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def get_daily_cap_count(tool: str) -> int:
    """Read today's cap counter for `tool`, without incrementing."""
    with _conn() as conn:
        _init_schema(conn)
        day = _today()
        cur = conn.execute(
            "SELECT count FROM daily_caps WHERE tool = ? AND day = ?",
            (tool, day),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def get_counts() -> Dict[str, int]:
    """All-time per-tool count."""
    with _conn() as conn:
        _init_schema(conn)
        cur = conn.execute("SELECT tool, SUM(count) FROM invocations GROUP BY tool")
        return {tool: int(total) for tool, total in cur.fetchall()}


def first_invocation_banner() -> Optional[str]:
    """Return banner string on first invocation in this process, then None."""
    global _FIRST_INVOCATION_SHOWN
    if _FIRST_INVOCATION_SHOWN:
        return None
    _FIRST_INVOCATION_SHOWN = True
    from milo_usage_forecaster import __version__
    return (
        f"# Milo Usage Forecaster v{__version__} — telemetry stays local; "
        "opt-in reporting in v0.2"
    )


def reset_for_tests() -> None:
    """Test-only: wipe state."""
    global _FIRST_INVOCATION_SHOWN
    _FIRST_INVOCATION_SHOWN = False
    home = _home_dir()
    if (home / DB_NAME).exists():
        (home / DB_NAME).unlink()
    if (home / INSTALL_ID_NAME).exists():
        (home / INSTALL_ID_NAME).unlink()
