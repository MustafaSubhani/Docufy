"""Record and replay model replies so agent tests do not need API keys or Ollama.

A cassette stores the reply text under a hash of the call. The date line in the
system prompt is ignored, so the same call matches later in the day.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_TODAY = re.compile(r"Today is .+?Your training data may be out of date\.", re.S)


class CassetteError(RuntimeError):
    pass


class Cassette:
    def __init__(self, path: str | Path, mode: str = "replay"):
        if mode not in {"replay", "record"}:
            raise CassetteError("cassette mode must be replay or record")
        self.path = Path(path)
        self.mode = mode
        self.entries: list[dict] = []
        if self.path.is_file():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = list(loaded or [])
        self._index = {item["key"]: item["text"] for item in self.entries if item.get("key")}

    def replay(self, model: str, messages: list[dict], schema: dict | None) -> str:
        found = self._index.get(self.key(model, messages, schema))
        if found is None:
            raise CassetteError("no cassette entry for this call")
        return found

    def record(self, model: str, messages: list[dict], schema: dict | None, text: str) -> None:
        key = self.key(model, messages, schema)
        if self._index.get(key) == text:
            return
        self._index[key] = text
        self.entries.append({"key": key, "model": model, "text": text})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=2), encoding="utf-8")

    def key(self, model: str, messages: list[dict], schema: dict | None) -> str:
        body = {
            "model": model,
            "messages": [{"role": item.get("role"), "content": _stable(str(item.get("content") or ""))} for item in messages],
            "schema": schema or None,
        }
        raw = json.dumps(body, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _stable(text: str) -> str:
    return _TODAY.sub("Today is (date). Your training data may be out of date.", text)
