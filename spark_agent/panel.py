"""Personal settings data: runs, health, catalog, and the activity log.

Keys are not written here. The activity log stores a short note, never a secret.
"""

from __future__ import annotations

import csv
import io
import re
import time
from pathlib import Path

from spark_docs.brand import load_facts

from . import store
from .governor import describe
from .llm import Router
from .tokens import lookup, probe_vram

ROOT = Path(__file__).resolve().parent.parent
_SAFE = re.compile(r"[A-Za-z0-9 .,:'/+_-]*")
_ollama_probe = None


def log_activity(kind: str, detail: str) -> None:
    store.ensure_panel()
    text = _public(detail)
    with store._lock:
        store.conn().execute(
            "INSERT INTO activity_log (user_id, at, kind, detail) VALUES ('local', ?, ?, ?)",
            (time.time(), kind[:40], text),
        )
        store.conn().commit()


def activity(limit: int = 12) -> list[dict]:
    store.ensure_panel()
    rows = store.conn().execute(
        "SELECT id, at, kind, detail FROM activity_log ORDER BY id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [{"id": row[0], "at": row[1], "kind": row[2], "detail": row[3]} for row in rows]


def record_tool(name: str, *, cost: float = 0, ok: bool = True, detail: str = "", run_id: str = "") -> None:
    store.ensure_panel()
    with store._lock:
        store.conn().execute(
            "INSERT INTO tool_calls (user_id, run_id, name, cost_usd, ok, detail, at) VALUES ('local', ?, ?, ?, ?, ?, ?)",
            (run_id, name[:80], float(cost), 1 if ok else 0, _public(detail), time.time()),
        )
        store.conn().commit()


def tools(limit: int = 12) -> list[dict]:
    store.ensure_panel()
    rows = store.conn().execute(
        "SELECT id, name, cost_usd, ok, detail, at FROM tool_calls ORDER BY id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [
        {"id": row[0], "name": row[1], "cost_usd": round(row[2] or 0, 4), "ok": bool(row[3]), "detail": row[4], "at": row[5]}
        for row in rows
    ]


def list_runs(limit: int = 30) -> list[dict]:
    store.ensure_panel()
    rows = store.conn().execute(
        """
        SELECT r.id, r.kind, r.profile, r.status, r.started_at, r.source,
               COALESCE(SUM(c.input_tokens),0) + COALESCE(SUM(c.output_tokens),0),
               COALESCE(SUM(c.cost_usd),0)
        FROM runs r LEFT JOIN llm_calls c ON c.run_id = r.id
        GROUP BY r.id ORDER BY r.started_at DESC LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [
        {
            "id": row[0],
            "kind": row[1] or "",
            "profile": row[2] or "",
            "status": row[3] or "",
            "started_at": row[4],
            "source": Path(row[5] or "").name,
            "tokens": row[6],
            "cost_usd": round(row[7] or 0, 4),
        }
        for row in rows
    ]


def usage_csv() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["at", "role", "provider", "model", "input_tokens", "output_tokens", "cost_usd", "finish_reason"])
    rows = store.conn().execute(
        "SELECT at, role, provider, model, input_tokens, output_tokens, cost_usd, finish_reason FROM llm_calls ORDER BY at DESC LIMIT 500"
    )
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def catalog(profile: str = "") -> list[dict]:
    router = Router(profile=profile or "")
    roles = (router.config.get("profiles") or {}).get(router.profile) or {}
    table = router.config.get("models") or {}
    rows = []
    for role, name in roles.items():
        card = lookup(str(name), table=table)
        rows.append(
            {
                "role": role,
                "model": str(name),
                "context": card.context,
                "max_output": card.max_output,
                "vision": card.vision,
            }
        )
    return rows


def lanes(profile: str = "") -> list[str]:
    return describe(Router(profile=profile or ""))


def brand_card() -> dict:
    company = load_facts()["company"]
    return {
        "name": company.get("name") or "",
        "phone": company.get("phone") or "",
        "email": company.get("email") or "",
        "website": company.get("website") or "",
    }


def memories() -> list[dict]:
    from .memory import list_memories

    rows = []
    for item in list_memories()[:20]:
        rows.append(
            {
                "id": item.get("id") or "",
                "status": item.get("status") or "",
                "scope": item.get("scope") or "",
                "text": item.get("mistake") or "",
            }
        )
    return rows


def health() -> dict:
    from .studio import load_settings

    settings = load_settings()
    return {
        "ollama": ollama_status(settings.get("ollama_url") or ""),
        "vram_bytes": probe_vram(),
        "disk_bytes": _disk_bytes(),
        "memory_percent": _memory_percent(),
        "errors": _errors(),
    }


def ollama_status(url: str = "") -> dict:
    if _ollama_probe is not None:
        return _ollama_probe(url)
    if not url:
        from .studio import load_settings

        url = load_settings().get("ollama_url") or ""
    try:
        import httpx

        response = httpx.get(f"{str(url).rstrip('/')}/api/tags", timeout=0.8)
    except Exception:
        return {"ok": False, "detail": "not reachable"}
    if response.status_code == 200:
        return {"ok": True, "detail": "reachable"}
    return {"ok": False, "detail": f"status {response.status_code}"}


def snapshot(profile: str = "") -> dict:
    from .secrets import status

    return {
        "keys": status(),
        "runs": list_runs(),
        "tools": tools(),
        "health": health(),
        "catalog": catalog(profile),
        "lanes": lanes(profile),
        "memory": memories(),
        "brand": brand_card(),
        "activity": activity(),
    }


def _public(detail: str) -> str:
    clean = " ".join(str(detail).split())[:240]
    if not _SAFE.fullmatch(clean) or (len(clean) > 40 and " " not in clean):
        return "updated"
    return clean


def _disk_bytes() -> int:
    root = ROOT / "workspace"
    if not root.is_dir():
        return 0
    total = 0
    seen = 0
    for path in root.rglob("*"):
        if seen > 4000:
            break
        seen += 1
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _memory_percent() -> float:
    from .governor import system_memory_percent

    return system_memory_percent()


def _errors() -> list[dict]:
    rows = store.conn().execute(
        "SELECT at, model, error FROM llm_calls WHERE error IS NOT NULL AND error != '' ORDER BY id DESC LIMIT 5"
    ).fetchall()
    return [{"at": row[0], "model": row[1] or "", "error": _public(row[2] or "")} for row in rows]
