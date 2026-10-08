"""Token pre-flight, model cards, and cut-off recovery.

Counting uses a character estimate unless the caller passes a tokenizer.
Ollama still receives num_ctx and num_predict on every request.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_FOLLOWING = re.compile(r"the following:\s*$", re.I)
_MIDWORD = re.compile(r"\w-\s*$")
_BARE_ITEM = re.compile(r"[-*+]|\d{1,2}[.)]")
_BLOCK = re.compile(r"\[\[(task|target|brief|evidence|history)\]\]\n?")


class TokenError(RuntimeError):
    pass


def estimate_tokens(text: str) -> int:
    return int(len(text) / 3.5) + 8


@dataclass
class ModelCard:
    name: str
    context: int = 8192
    max_output: int = 4096
    tools: bool = False
    json_schema: bool = True
    vision: bool = False
    prompt_cache: bool = False
    pdf: bool = False
    layers: int = 0
    kv_heads: int = 0
    head_dim: int = 0
    weight_gb: float = 0.0
    input_usd_per_mtok: float = 0.0
    output_usd_per_mtok: float = 0.0

    @property
    def weight_bytes(self) -> int:
        return int(self.weight_gb * (1024**3))


@dataclass
class Recovery:
    text: str
    data: object = None
    status: str = "failed"
    steps: list[str] = field(default_factory=list)


def lookup(name: str, *, table: dict | None = None, show: dict | None = None, litellm_cost: dict | None = None) -> ModelCard:
    """Local overrides win over a LiteLLM cost row. An Ollama /api/show payload wins over both."""
    card = ModelCard(name=name)
    row = _cost_row(name, litellm_cost or {})
    if row:
        _apply_cost(card, row)
    overrides = (table or {}).get(name) or {}
    _apply_map(card, overrides)
    if show:
        _apply_map(card, card_from_show(show))
    card.context = max(1, int(card.context))
    card.max_output = max(1, int(card.max_output))
    return card


def card_from_show(show: dict) -> dict:
    info = show.get("model_info") or {}
    arch = str(info.get("general.architecture") or "")

    def pick(*suffixes):
        for key, value in info.items():
            if arch and key.startswith(arch + ".") and any(key.endswith(suffix) for suffix in suffixes):
                return value
        for key, value in info.items():
            if any(key.endswith(suffix) for suffix in suffixes):
                return value
        return None

    found: dict = {}
    context = pick(".context_length")
    layers = pick(".block_count")
    kv_heads = pick(".attention.head_count_kv")
    heads = pick(".attention.head_count")
    embed = pick(".embedding_length")
    key_length = pick(".attention.key_length")
    if context:
        found["context"] = int(context)
    if layers:
        found["layers"] = int(layers)
    if kv_heads:
        found["kv_heads"] = int(kv_heads)
    if key_length:
        found["head_dim"] = int(key_length)
    elif embed and heads and int(heads) > 0:
        found["head_dim"] = int(embed) // int(heads)
    caps = [str(item).lower() for item in show.get("capabilities") or []]
    if caps:
        found["vision"] = "vision" in caps
    return found


def fetch_show(name: str, url: str = "http://127.0.0.1:11434", post=None) -> dict:
    endpoint = f"{url.rstrip('/')}/api/show"

    def _post(path, body):
        import httpx

        response = httpx.post(path, json=body, timeout=10)
        response.raise_for_status()
        return response.json()

    sender = post or _post
    return sender(endpoint, {"name": name})


def kv_bytes(layers: int, kv_heads: int, head_dim: int, num_ctx: int, slots: int = 1, bytes_per_value: int = 2) -> int:
    """2 x layers x kv heads x head dim x bytes x context x parallel slots."""
    return 2 * int(layers) * int(kv_heads) * int(head_dim) * int(bytes_per_value) * int(num_ctx) * max(1, int(slots))


def choose_num_ctx(
    card: ModelCard,
    *,
    free_vram_bytes: int | None,
    configured: int = 16384,
    flash: bool = False,
    kv_q8: bool = False,
    parallel: int = 1,
) -> int:
    """16k unless flash attention and q8 KV are on. Shrink further when the weights and KV do not fit."""
    ceiling = 32768 if flash and kv_q8 else 16384
    wanted = min(max(2048, int(configured)), ceiling)
    if free_vram_bytes is None or card.layers <= 0 or card.kv_heads <= 0 or card.head_dim <= 0:
        return wanted
    bytes_per = 1 if kv_q8 else 2
    ctx = wanted
    while ctx >= 2048:
        needed = card.weight_bytes + kv_bytes(card.layers, card.kv_heads, card.head_dim, ctx, parallel, bytes_per)
        if needed <= int(free_vram_bytes):
            return ctx
        ctx //= 2
    return 2048


def probe_vram() -> int | None:
    try:
        import pynvml
    except Exception:
        return None
    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        return int(info.free)
    except Exception:
        return None


def ollama_options(num_ctx: int, max_tokens: int, temperature: float) -> dict:
    return {
        "num_ctx": int(num_ctx),
        "num_predict": int(max_tokens),
        "temperature": temperature,
        "repeat_penalty": 1.08,
    }


def repeated_ngram(text: str, n: int = 8, times: int = 4) -> bool:
    words = text.split()
    if len(words) < n * 2:
        return False
    counts: dict[tuple[str, ...], int] = {}
    for index in range(len(words) - n + 1):
        gram = tuple(words[index : index + n])
        counts[gram] = counts.get(gram, 0) + 1
        if counts[gram] >= times:
            return True
    return False


def cutoff_kind(reason: str, text: str = "", *, schema: bool = False, stalled: bool = False) -> str:
    raw = (reason or "").strip()
    if raw.lower() in {"length", "max_tokens"} or raw == "MAX_TOKENS":
        return "length"
    if stalled:
        return "stalled"
    if text and repeated_ngram(text):
        return "repetition"
    if schema and text and not _json_complete(text):
        return "json"
    return ""


def fit_user(system: str, user: str, max_output: int, context: int, counter=estimate_tokens) -> str:
    """Return the user text unchanged when it fits. Otherwise drop history, then the lowest evidence."""
    room = int(int(context) * 0.95)
    if counter(system) + counter(user) + int(max_output) <= room:
        return user
    return compress(system, user, max_output, context, counter)


def compress(system: str, user: str, max_output: int, context: int, counter=estimate_tokens) -> str:
    blocks = parse_blocks(user)
    budget = int(int(context) * 0.95) - int(max_output) - counter(system)
    if budget < counter(blocks["task"]) + 1:
        raise TokenError("the instructions do not fit the context window")
    history = blocks["history"]
    evidence = list(blocks["evidence"])
    brief = blocks["brief"]
    target = blocks["target"]
    task = blocks["task"]

    def size() -> int:
        return counter(render_blocks(task, target, brief, evidence, history))

    if size() > budget and history:
        history = _shorten(history)
    if size() > budget:
        history = ""
    while size() > budget and evidence:
        evidence.pop()
    if size() > budget and brief:
        brief = ""
    if size() > budget and target:
        target = _trim_to(target, max(1, budget - counter(render_blocks(task, "", "", [], "")) - 8), counter)
    if size() > budget:
        raise TokenError("the instructions do not fit the context window")
    rendered = render_blocks(task, target, brief, evidence, history)
    if not any(marker in user for marker in ("[[task]]", "[[target]]", "[[brief]]", "[[evidence]]", "[[history]]")):
        return target
    return rendered


def parse_blocks(user: str) -> dict:
    if "[[task]]" not in user and "[[target]]" not in user and "[[evidence]]" not in user and "[[history]]" not in user and "[[brief]]" not in user:
        return {"task": "", "target": user, "brief": "", "evidence": [], "history": ""}
    parts = _BLOCK.split(user)
    blocks = {"task": "", "target": "", "brief": "", "evidence": [], "history": ""}
    # split keeps the delimiters: text, name, text, name, text...
    index = 1
    while index + 1 < len(parts):
        name = parts[index]
        body = parts[index + 1].strip()
        if name == "evidence":
            if body:
                blocks["evidence"].append(body)
        else:
            blocks[name] = body
        index += 2
    return blocks


def render_blocks(task: str, target: str, brief: str, evidence: list[str], history: str) -> str:
    parts = []
    if task:
        parts.append("[[task]]\n" + task)
    if target:
        parts.append("[[target]]\n" + target)
    if brief:
        parts.append("[[brief]]\n" + brief)
    for item in evidence:
        parts.append("[[evidence]]\n" + item)
    if history:
        parts.append("[[history]]\n" + history)
    return "\n".join(parts)


def cut_at_sentence(text: str) -> str:
    matches = list(re.finditer(r"[.!?](?:\s|$)", text))
    if not matches:
        return text.rstrip()
    return text[: matches[-1].end()].rstrip()


def stitch(previous: str, continuation: str) -> str:
    base = cut_at_sentence(previous)
    extra = continuation.strip()
    if not extra:
        return base
    if extra.startswith(base):
        return extra
    window = min(len(base), len(extra), 400)
    for size in range(window, 11, -1):
        if base.endswith(extra[:size]):
            return base + extra[size:]
    if base.endswith((".", "!", "?")):
        return f"{base} {extra}"
    return f"{base} {extra}"


def salvage_json(text: str) -> tuple[object | None, bool]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0].strip()
    try:
        return json.loads(raw), True
    except json.JSONDecodeError:
        pass
    closed = _closed_objects(raw)
    partial = _jiter(raw)
    if isinstance(partial, dict):
        for key, value in list(partial.items()):
            if isinstance(value, list):
                partial[key] = [item for item in value if item in closed]
        if any(partial.get(key) for key in partial if isinstance(partial.get(key), list)):
            return partial, False
    if isinstance(partial, list):
        kept = [item for item in partial if item in closed]
        if kept:
            return kept, False
    if closed:
        return closed, False
    return None, False


def split_task(text: str) -> tuple[str, str]:
    body = text.strip()
    if not body:
        return "", ""
    paragraphs = re.split(r"\n\s*\n", body)
    if len(paragraphs) >= 2:
        mid = len(paragraphs) // 2
        return "\n\n".join(paragraphs[:mid]).strip(), "\n\n".join(paragraphs[mid:]).strip()
    sentences = [part for part in _SENTENCE.split(body) if part.strip()]
    if len(sentences) >= 2:
        mid = max(1, len(sentences) // 2)
        return " ".join(sentences[:mid]).strip(), " ".join(sentences[mid:]).strip()
    words = body.split()
    mid = max(1, len(words) // 2)
    return " ".join(words[:mid]), " ".join(words[mid:])


def recover(text: str, *, reason: str, call, schema: dict | None = None, max_output: int = 1024, fallback: str = "", source: str = "") -> Recovery:
    """Continue, salvage, raise the limit, split, then fall back. A failed step does not stop the ladder."""
    steps: list[str] = []

    def ask(step: str, payload: dict) -> str:
        steps.append(step)
        got = call(step, payload)
        return "" if got is None else str(got)

    if schema is None and text.strip() and reason in {"length", "repetition", "stalled"}:
        more = ask("continue", {"partial": cut_at_sentence(text), "max_output": max_output})
        if more and not repeated_ngram(more):
            return Recovery(stitch(text, more), None, "continued", steps)
    if schema is not None and text.strip():
        value, complete = salvage_json(text)
        if value is not None and complete:
            return Recovery(text, value, "salvaged", steps)
        if value is not None:
            more = ask("missing", {"have": value, "max_output": max_output})
            if more:
                parsed, done = salvage_json(more)
                if parsed is not None and done:
                    return Recovery(more, parsed, "salvaged", steps)
    raised = min(int(max_output) * 2, 16384)
    more = ask("raise", {"max_output": raised, "partial": text})
    if more and not repeated_ngram(more) and (schema is None or salvage_json(more)[1]):
        data = salvage_json(more)[0] if schema is not None else None
        return Recovery(more, data, "raised", steps)
    left, right = split_task(source or text)
    first = ask("split", {"part": left, "index": 0, "max_output": max_output})
    second = ask("split", {"part": right, "index": 1, "max_output": max_output})
    if first and second:
        joined = stitch(first, second) if schema is None else first.strip() + "\n" + second.strip()
        return Recovery(joined, None, "split", steps)
    if fallback:
        more = ask("fallback", {"model": fallback, "max_output": max_output})
        if more:
            return Recovery(more, None, "fallback", steps)
    return Recovery(text, None, "failed", steps)


def continuation_messages(messages: list[dict], partial: str) -> list[dict]:
    kept = cut_at_sentence(partial)
    return messages + [
        {"role": "assistant", "content": kept},
        {"role": "user", "content": "Continue from the last complete sentence. Do not repeat text already written."},
    ]


def truncation_issues(text: str) -> list[str]:
    body = text.strip()
    if not body:
        return []
    issues = []
    if _FOLLOWING.search(body):
        issues.append("text ends on 'the following:'")
    if _MIDWORD.search(body):
        issues.append("text ends mid-word")
    if body.count("```") % 2 == 1:
        issues.append("a code fence is unclosed")
    last = [line.strip() for line in body.splitlines() if line.strip()][-1]
    if _BARE_ITEM.fullmatch(last):
        issues.append("a list item is unclosed")
    return issues


def idempotency_key(run_id: str | None, role: str, model: str, messages: list, max_tokens: int, schema: dict | None) -> str:
    payload = {
        "run": run_id or "",
        "role": role,
        "model": model,
        "max_tokens": max_tokens,
        "schema": schema or {},
        "messages": messages,
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def describe_model(name: str, table: dict | None = None, *, configured: int = 16384) -> list[str]:
    card = lookup(name, table=table)
    chosen = choose_num_ctx(
        card,
        free_vram_bytes=None,
        configured=configured,
        flash=os.environ.get("OLLAMA_FLASH_ATTENTION") == "1",
        kv_q8=os.environ.get("OLLAMA_KV_CACHE_TYPE") == "q8_0",
    )
    return [
        f"model {card.name}",
        f"context {card.context}",
        f"max output {card.max_output}",
        f"vision {'yes' if card.vision else 'no'}",
        f"json {'yes' if card.json_schema else 'no'}",
        f"num_ctx {chosen}",
    ]


def _cost_row(name: str, table: dict) -> dict | None:
    if name in table:
        return table[name]
    short = name.split("/", 1)[-1]
    if short in table:
        return table[short]
    return None


def _apply_cost(card: ModelCard, row: dict) -> None:
    if row.get("max_input_tokens"):
        card.context = int(row["max_input_tokens"])
    output = row.get("max_output_tokens") or row.get("max_tokens")
    if output:
        card.max_output = int(output)
    if "supports_vision" in row:
        card.vision = bool(row["supports_vision"])
    if "supports_function_calling" in row:
        card.tools = bool(row["supports_function_calling"])
    if row.get("input_cost_per_token"):
        card.input_usd_per_mtok = float(row["input_cost_per_token"]) * 1_000_000
    if row.get("output_cost_per_token"):
        card.output_usd_per_mtok = float(row["output_cost_per_token"]) * 1_000_000


def _apply_map(card: ModelCard, row: dict) -> None:
    for key in ("context", "max_output", "layers", "kv_heads", "head_dim"):
        if row.get(key):
            setattr(card, key, int(row[key]))
    for key in ("weight_gb", "input_usd_per_mtok", "output_usd_per_mtok"):
        if row.get(key):
            setattr(card, key, float(row[key]))
    for key in ("tools", "json_schema", "vision", "prompt_cache", "pdf"):
        if key in row:
            setattr(card, key, bool(row[key]))


def _shorten(text: str) -> str:
    words = text.split()
    if len(words) <= 40:
        return text
    return " ".join(words[:20]) + " ... " + " ".join(words[-12:])


def _trim_to(text: str, budget: int, counter) -> str:
    if budget <= 0:
        return ""
    kept = text
    while len(kept) > 1 and counter(kept) > budget:
        nxt = kept[: max(1, int(len(kept) * 0.8))]
        kept = kept[:-1] if nxt == kept else nxt
    if counter(kept) > budget:
        return ""
    return kept.rstrip()


def _closed_objects(text: str) -> list:
    decoder = json.JSONDecoder()
    found = []
    index = 0
    while index < len(text):
        if text[index] != "{":
            index += 1
            continue
        try:
            obj, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index += 1
            continue
        if isinstance(obj, dict):
            found.append(obj)
        index = max(end, index + 1)
    return found


def _jiter(text: str):
    try:
        import jiter
    except Exception:
        return None
    try:
        return jiter.from_json(text.encode("utf-8"), partial_mode="trailing-strings")
    except Exception:
        return None


def _json_complete(text: str) -> bool:
    _value, complete = salvage_json(text)
    return complete
