"""Load theme tokens (themes/<name>/tokens.yaml) with attribute access."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
THEMES = ROOT / "themes"


class Tok(dict):
    """dict with attribute access; nested dicts become Tok."""

    def __getattr__(self, key: str) -> Any:
        try:
            value = self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc
        return value

    def get_path(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


def _wrap(value: Any) -> Any:
    if isinstance(value, dict):
        return Tok({k: _wrap(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


@lru_cache(maxsize=8)
def load(name: str = "spark") -> Tok:
    path = THEMES / name / "tokens.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _wrap(data)


def color(name_or_hex: str, theme: str = "spark") -> str:
    """Resolve a colour token name to a hex string."""
    if name_or_hex.startswith("#"):
        return name_or_hex
    return load(theme).colors[name_or_hex]
