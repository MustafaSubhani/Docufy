"""API keys in Windows Credential Manager. The value never goes into SQLite or back to the browser."""

from __future__ import annotations

SERVICE = "spark-doc-agent"
NAMES = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "gemini": "Gemini",
    "stability": "Stability",
    "fal": "Flux",
    "tavily": "Tavily",
    "serper": "Serper",
    "brave": "Brave",
    "exa": "Exa",
}

_vault = None


class SecretError(RuntimeError):
    pass


class DictVault:
    """In-memory stand-in used by tests. It is not the Credential Manager."""

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, name: str) -> str | None:
        return self.data.get((service, name))

    def set_password(self, service: str, name: str, secret: str) -> None:
        self.data[(service, name)] = secret

    def delete_password(self, service: str, name: str) -> None:
        self.data.pop((service, name), None)


def use(vault) -> None:
    global _vault
    _vault = vault


def status() -> list[dict]:
    backend = _backend()
    rows = []
    for name, label in NAMES.items():
        rows.append({"name": name, "label": label, "set": bool(_read(backend, name))})
    return rows


def put(name: str, secret: str) -> dict:
    if name not in NAMES:
        raise SecretError("unknown key")
    value = str(secret or "").strip()
    if not value or len(value) > 400 or any(ch.isspace() for ch in value):
        raise SecretError("the key is empty or not a single token")
    _backend().set_password(SERVICE, name, value)
    from .panel import log_activity

    log_activity("key", f"stored {name}")
    return {"name": name, "set": True}


def clear(name: str) -> dict:
    if name not in NAMES:
        raise SecretError("unknown key")
    backend = _backend()
    try:
        backend.delete_password(SERVICE, name)
    except Exception:
        pass
    from .panel import log_activity

    log_activity("key", f"cleared {name}")
    return {"name": name, "set": False}


def test_key(name: str, probe=None) -> dict:
    if name == "ollama":
        from .panel import ollama_status

        return ollama_status()
    if name not in NAMES:
        raise SecretError("unknown key")
    if not _read(_backend(), name):
        return {"ok": False, "detail": "No key stored"}
    if probe is not None:
        return probe(name)
    return {"ok": True, "detail": "The key is stored in Credential Manager"}


def _backend():
    if _vault is not None:
        return _vault
    try:
        import keyring
    except Exception as exc:
        raise SecretError("Credential Manager is unavailable") from exc
    return keyring


def value(name: str) -> str:
    """Return a stored key for a provider call. Callers must not log it."""
    if name not in NAMES:
        return ""
    return _read(_backend(), name)


def _read(backend, name: str) -> str:
    try:
        found = backend.get_password(SERVICE, name)
    except Exception:
        return ""
    return found or ""
