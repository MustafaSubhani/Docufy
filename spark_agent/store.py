"""Local SQLite store: runs, run events, and every LLM call (tokens, latency, finish reason)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "workspace" / "spark.db"

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, user_id TEXT DEFAULT 'local', kind TEXT, source TEXT, profile TEXT,
  status TEXT, started_at REAL, finished_at REAL, output TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, at REAL, kind TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS checkpoints (
  run_id TEXT PRIMARY KEY, node TEXT, state TEXT, updated_at REAL
);
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, user_id TEXT DEFAULT 'local', role TEXT,
  provider TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER,
  cost_usd REAL, latency_s REAL, finish_reason TEXT, retries INTEGER, error TEXT, at REAL
);
CREATE TABLE IF NOT EXISTS tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT 'local', run_id TEXT, name TEXT,
  cost_usd REAL, ok INTEGER, detail TEXT, at REAL
);
CREATE TABLE IF NOT EXISTS activity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT 'local', at REAL, kind TEXT, detail TEXT
);
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        from .migrate import apply

        apply(_conn, DB_PATH)
    return _conn


def ensure_panel() -> None:
    c = conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS tool_calls (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT 'local', run_id TEXT, name TEXT,
          cost_usd REAL, ok INTEGER, detail TEXT, at REAL
        );
        CREATE TABLE IF NOT EXISTS activity_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT 'local', at REAL, kind TEXT, detail TEXT
        );
        """
    )
    c.commit()


def start_run(kind: str, source: str, profile: str) -> str:
    run_id = uuid.uuid4().hex[:12]
    with _lock:
        conn().execute(
            "INSERT INTO runs (id, kind, source, profile, status, started_at) VALUES (?,?,?,?,?,?)",
            (run_id, kind, source, profile, "running", time.time()),
        )
        conn().commit()
    return run_id


def finish_run(run_id: str, status: str, output: str = "", summary: dict | None = None) -> None:
    with _lock:
        conn().execute(
            "UPDATE runs SET status=?, finished_at=?, output=?, summary=? WHERE id=?",
            (status, time.time(), output, json.dumps(summary or {}), run_id),
        )
        conn().commit()


def event(run_id: str | None, kind: str, data: dict | None = None) -> None:
    if not run_id:
        return
    with _lock:
        conn().execute("INSERT INTO run_events (run_id, at, kind, data) VALUES (?,?,?,?)", (run_id, time.time(), kind, json.dumps(data or {})))
        conn().commit()


def record_call(**row) -> None:
    cols = ["run_id", "role", "provider", "model", "input_tokens", "output_tokens", "cached_tokens", "cost_usd", "latency_s", "finish_reason", "retries", "error"]
    with _lock:
        conn().execute(
            f"INSERT INTO llm_calls ({', '.join(cols)}, at) VALUES ({', '.join('?' for _ in cols)}, ?)",
            [row.get(c) for c in cols] + [time.time()],
        )
        conn().commit()


def set_status(run_id: str, status: str) -> None:
    with _lock:
        conn().execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))
        conn().commit()


def save_checkpoint(run_id: str, node: str, state: dict) -> None:
    with _lock:
        conn().execute(
            "INSERT OR REPLACE INTO checkpoints (run_id, node, state, updated_at) VALUES (?,?,?,?)",
            (run_id, node, json.dumps(state), time.time()),
        )
        conn().commit()


def load_checkpoint(run_id: str) -> dict | None:
    row = conn().execute("SELECT node, state FROM checkpoints WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        return None
    state = json.loads(row[1])
    state["node"] = row[0]
    return state


def events_since(run_id: str, after: int = 0) -> list[dict]:
    rows = conn().execute(
        "SELECT id, kind, data, at FROM run_events WHERE run_id=? AND id>? ORDER BY id",
        (run_id, int(after)),
    ).fetchall()
    return [{"id": row[0], "kind": row[1], "data": json.loads(row[2] or "{}"), "at": row[3]} for row in rows]


def usage(run_id: str) -> dict:
    cur = conn().execute(
        "SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), COALESCE(SUM(cost_usd),0), COALESCE(SUM(latency_s),0) FROM llm_calls WHERE run_id=?",
        (run_id,),
    )
    n, tin, tout, cost, secs = cur.fetchone()
    return {"calls": n, "input_tokens": tin, "output_tokens": tout, "cost_usd": round(cost, 4), "llm_seconds": round(secs, 1)}
