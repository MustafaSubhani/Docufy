"""Model router: roles -> models per profile, JSON-schema outputs, token guard, retries, accounting.

Local models (ollama/...) go straight to the Ollama HTTP API so num_ctx, num_predict, schema-constrained
output and done_reason are fully controlled. API models (anthropic/..., openai/...) go through LiteLLM.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from . import store
from .tokens import (
    TokenError,
    choose_num_ctx,
    continuation_messages,
    cutoff_kind,
    estimate_tokens,
    fit_user,
    idempotency_key,
    lookup,
    ollama_options,
    probe_vram,
    repeated_ngram,
    stitch,
)

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "models.yaml"


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    data: Any = None
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""
    latency_s: float = 0.0
    retries: int = 0
    cost_usd: float = 0.0


def load_config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) if CONFIG.is_file() else {}


def now_line() -> str:
    now = datetime.now().astimezone()
    return f"Today is {now:%A, %d %B %Y}, {now:%H:%M} (UTC{now:%z}). Your training data may be out of date."


@dataclass
class Router:
    profile: str = ""
    run_id: str | None = None
    config: dict = field(default_factory=load_config)
    fake: Any = None  # FakeLLM for tests: callable(model, messages, schema) -> str
    cassette: Any = None
    seen: dict = field(default_factory=dict)

    def __post_init__(self):
        self.profile = self.profile or os.environ.get("SPARK_PROFILE") or self.config.get("active_profile", "dev")
        raw = os.environ.get("SPARK_CASSETTE", "").strip()
        if self.cassette is None and raw:
            from .cassettes import Cassette

            self.cassette = Cassette(raw, os.environ.get("SPARK_CASSETTE_MODE", "replay"))

    def model_for(self, role: str) -> str:
        prof = (self.config.get("profiles") or {}).get(self.profile) or {}
        model = prof.get(role) or prof.get("writer")
        if not model:
            raise LLMError(f"no model configured for role {role!r} in profile {self.profile!r}")
        return model

    def _check_budget(self) -> None:
        budgets = self.config.get("budgets") or {}
        cap = float(budgets.get("per_run_usd") or 0)
        if cap > 0 and self.run_id:
            spent = float(store.usage(self.run_id)["cost_usd"])
            if spent >= cap:
                raise LLMError(f"run budget ${cap:.2f} reached (${spent:.2f} spent)")
        monthly = float(budgets.get("monthly_usd") or 0)
        if budgets.get("hard_stop") and monthly > 0:
            start = time.time() - 30 * 86400
            row = store.conn().execute("SELECT COALESCE(SUM(cost_usd),0) FROM llm_calls WHERE at>=?", (start,)).fetchone()
            spent = float(row[0] or 0)
            if spent >= monthly:
                raise LLMError(f"monthly budget ${monthly:.2f} reached (${spent:.2f} spent)")

    def _fallback_model(self, role: str, model: str) -> str | None:
        """Cloud profiles can switch provider. Local Ollama calls stay on the machine."""
        if model.startswith("ollama/"):
            return None
        if (self.config.get("privacy") or {}).get("local_only"):
            return None
        alt_profile = (self.config.get("fallbacks") or {}).get(self.profile)
        if not alt_profile:
            return None
        prof = (self.config.get("profiles") or {}).get(alt_profile) or {}
        alt = prof.get(role) or prof.get("writer")
        if not alt or alt == model or str(alt).startswith("ollama/"):
            return None
        return str(alt)

    # ------------------------------------------------------------------
    def complete(
        self,
        role: str,
        system: str,
        user: str,
        schema: dict | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.1,
    ) -> LLMResult:
        """Run one call. With a schema, returns parsed JSON in .data (validated, repaired, retried)."""
        limits = self.config.get("limits") or {}
        max_tokens = max_tokens or int(limits.get("max_output_tokens", 4096))
        retries = int(limits.get("retries", 2))
        system = f"{system}\n\n{now_line()}"
        model = self.model_for(role)
        card = lookup(model, table=self.config.get("models") or {})
        try:
            user = fit_user(system, user, max_tokens, card.context)
        except TokenError as exc:
            raise LLMError(str(exc)) from exc
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        key = idempotency_key(self.run_id, role, model, messages, max_tokens, schema)
        cached = self.seen.get(key)
        if cached is not None:
            return cached
        base_messages = messages
        carried = ""
        last_err = ""
        fell_back = False
        for attempt in range(retries + 1):
            self._check_budget()
            t0 = time.time()
            try:
                res = self._call(model, messages, schema, max_tokens, temperature)
            except LLMError:
                raise  # deterministic (context too large, model disabled): retrying cannot help
            except Exception as exc:  # network, provider, timeout
                last_err = str(exc)
                store.record_call(run_id=self.run_id, role=role, provider=model.split("/")[0], model=model, latency_s=time.time() - t0, retries=attempt, error=last_err[:500])
                alt = None if fell_back else self._fallback_model(role, model)
                if alt:
                    model = alt
                    fell_back = True
                    continue
                if self.fake is None:
                    time.sleep(min(2**attempt, 8))
                continue
            res.retries = attempt
            if not res.cost_usd:
                res.cost_usd = self.price(model, res.input_tokens, res.output_tokens)
            store.record_call(
                run_id=self.run_id, role=role, provider=model.split("/")[0], model=model,
                input_tokens=res.input_tokens, output_tokens=res.output_tokens, cached_tokens=0, cost_usd=res.cost_usd,
                latency_s=res.latency_s, finish_reason=res.finish_reason, retries=attempt, error=None,
            )
            kind = cutoff_kind(res.finish_reason, res.text, schema=schema is not None)
            if kind in {"length", "repetition"} or repeated_ngram(res.text):
                if self.run_id and res.text:
                    store.save_checkpoint(self.run_id, "llm", {"partial": res.text, "role": role, "model": model})
                last_err = "repetition" if kind == "repetition" or repeated_ngram(res.text) else "output cut off at max_tokens"
                if schema is None and res.text.strip() and not repeated_ngram(res.text):
                    carried = stitch(carried, res.text) if carried else res.text
                    messages = continuation_messages(base_messages, carried)
                max_tokens = min(max_tokens * 2, 16384)
                continue
            if schema is None:
                if carried:
                    res.text = stitch(carried, res.text)
                self.seen[key] = res
                return res
            try:
                res.data = parse_json(res.text)
                self.seen[key] = res
                return res
            except ValueError as exc:
                last_err = f"invalid JSON: {exc}"
                messages = messages + [
                    {"role": "assistant", "content": res.text[:4000]},
                    {"role": "user", "content": f"That was not valid JSON ({exc}). Reply with only the corrected JSON object."},
                ]
        raise LLMError(f"{role} call failed after {retries + 1} attempts: {last_err}")

    def price(self, model: str, input_tokens: int, output_tokens: int) -> float:
        card = lookup(model, table=self.config.get("models") or {})
        return round((input_tokens * card.input_usd_per_mtok + output_tokens * card.output_usd_per_mtok) / 1_000_000, 6)

    def stream(
        self,
        role: str,
        system: str,
        user: str,
        on_text,
        max_tokens: int | None = None,
        temperature: float = 0.3,
    ) -> LLMResult:
        """A plain reply that reports text as it arrives. Only local Ollama streams; other paths report once."""
        model = self.model_for(role)
        if self.fake is None and self.cassette is None and model.startswith("openai/") and not (self.config.get("privacy") or {}).get("local_only"):
            limits = self.config.get("limits") or {}
            messages = [{"role": "system", "content": f"{system}\n\n{now_line()}"}, {"role": "user", "content": user}]
            self._check_budget()
            t0 = time.time()
            try:
                res = self._openai(model.split("/", 1)[1], messages, None, max_tokens or int(limits.get("max_output_tokens", 4096)), temperature, on_text)
            except LLMError:
                raise
            except Exception as exc:
                store.record_call(run_id=self.run_id, role=role, provider="openai", model=model, latency_s=time.time() - t0, retries=0, error=str(exc)[:500])
                raise LLMError(str(exc)[:300]) from exc
            store.record_call(
                run_id=self.run_id, role=role, provider="openai", model=model,
                input_tokens=res.input_tokens, output_tokens=res.output_tokens, cached_tokens=0, cost_usd=res.cost_usd,
                latency_s=res.latency_s, finish_reason=res.finish_reason, retries=0, error=None,
            )
            return res
        if self.fake is not None or self.cassette is not None or not model.startswith("ollama/"):
            res = self.complete(role, system, user, max_tokens=max_tokens, temperature=temperature)
            on_text(res.text)
            return res
        limits = self.config.get("limits") or {}
        max_tokens = max_tokens or int(limits.get("max_output_tokens", 4096))
        system = f"{system}\n\n{now_line()}"
        card = lookup(model, table=self.config.get("models") or {})
        try:
            user = fit_user(system, user, max_tokens, card.context)
        except TokenError as exc:
            raise LLMError(str(exc)) from exc
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        self._check_budget()
        t0 = time.time()
        try:
            res = self._ollama_stream(model.split("/", 1)[1], messages, max_tokens, temperature, on_text)
        except LLMError:
            raise
        except httpx.HTTPError as exc:
            store.record_call(run_id=self.run_id, role=role, provider="ollama", model=model, latency_s=time.time() - t0, retries=0, error=str(exc)[:500])
            raise LLMError("the local model is not reachable") from exc
        store.record_call(
            run_id=self.run_id, role=role, provider="ollama", model=model,
            input_tokens=res.input_tokens, output_tokens=res.output_tokens, cached_tokens=0, cost_usd=0.0,
            latency_s=res.latency_s, finish_reason=res.finish_reason, retries=0, error=None,
        )
        return res

    # ------------------------------------------------------------------
    def _call(self, model: str, messages: list[dict], schema: dict | None, max_tokens: int, temperature: float) -> LLMResult:
        if self.fake is not None:
            text = self.fake(model, messages, schema)
            self._remember(model, messages, schema, text)
            return LLMResult(text=text, model=model, input_tokens=estimate_tokens(str(messages)), output_tokens=estimate_tokens(text), finish_reason="stop")
        if self.cassette is not None and self.cassette.mode == "replay":
            from .cassettes import CassetteError

            try:
                text = self.cassette.replay(model, messages, schema)
            except CassetteError as exc:
                raise LLMError(str(exc)) from exc
            return LLMResult(text=text, model=model, input_tokens=estimate_tokens(str(messages)), output_tokens=estimate_tokens(text), finish_reason="stop")
        if not str(model).startswith("ollama/") and (self.config.get("privacy") or {}).get("local_only"):
            raise LLMError("this run stays on the local model")
        provider, _, name = model.partition("/")
        if provider == "ollama":
            result = self._ollama(name, messages, schema, max_tokens, temperature)
        elif provider == "openai":
            result = self._openai(name, messages, schema, max_tokens, temperature)
        else:
            _apply_provider_keys()
            result = self._litellm(model, messages, schema, max_tokens, temperature)
        self._remember(model, messages, schema, result.text)
        return result

    def _remember(self, model: str, messages: list[dict], schema: dict | None, text: str) -> None:
        if self.cassette is not None and self.cassette.mode == "record":
            self.cassette.record(model, messages, schema, text)

    def _ollama_ctx(self, name: str, messages: list[dict], max_tokens: int) -> tuple[int, int]:
        cfg = self.config.get("ollama") or {}
        requested = int(cfg.get("num_ctx", 16384))
        card = lookup(f"ollama/{name}", table=self.config.get("models") or {})
        num_ctx = choose_num_ctx(
            card,
            free_vram_bytes=probe_vram(),
            configured=requested,
            flash=os.environ.get("OLLAMA_FLASH_ATTENTION") == "1",
            kv_q8=os.environ.get("OLLAMA_KV_CACHE_TYPE") == "q8_0",
        )
        prompt_tokens = estimate_tokens("".join(m["content"] for m in messages))
        if prompt_tokens + max_tokens > num_ctx:
            # Ollama silently truncates prompts beyond num_ctx; refuse instead of producing garbage
            raise LLMError(f"prompt (~{prompt_tokens} tokens) + output ({max_tokens}) exceeds num_ctx {num_ctx}")
        return num_ctx, prompt_tokens

    def _ollama_stream(self, name: str, messages: list[dict], max_tokens: int, temperature: float, on_text) -> LLMResult:
        cfg = self.config.get("ollama") or {}
        num_ctx, prompt_tokens = self._ollama_ctx(name, messages, max_tokens)
        body = {
            "model": name,
            "messages": messages,
            "stream": True,
            "keep_alive": cfg.get("keep_alive", "10m"),
            "options": ollama_options(num_ctx, max_tokens, temperature),
        }
        timeout = float((self.config.get("limits") or {}).get("request_timeout_s", 300))
        t0 = time.time()
        pieces: list[str] = []
        final: dict = {}
        shown = 0.0
        with httpx.stream("POST", f"{cfg.get('url', 'http://127.0.0.1:11434')}/api/chat", json=body, timeout=timeout) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                piece = (data.get("message") or {}).get("content") or ""
                if piece:
                    pieces.append(piece)
                    if time.time() - shown > 0.25:
                        on_text("".join(pieces))
                        shown = time.time()
                if data.get("done"):
                    final = data
                    break
                if len(pieces) % 40 == 0 and repeated_ngram("".join(pieces)):
                    final = {"done_reason": "length"}
                    break
        text = "".join(pieces)
        on_text(text)
        return LLMResult(
            text=text,
            model=f"ollama/{name}",
            input_tokens=int(final.get("prompt_eval_count") or prompt_tokens),
            output_tokens=int(final.get("eval_count") or estimate_tokens(text)),
            finish_reason="length" if final.get("done_reason") == "length" else "stop",
            latency_s=time.time() - t0,
        )

    def _ollama(self, name: str, messages: list[dict], schema: dict | None, max_tokens: int, temperature: float) -> LLMResult:
        cfg = self.config.get("ollama") or {}
        num_ctx, prompt_tokens = self._ollama_ctx(name, messages, max_tokens)
        body = {
            "model": name,
            "messages": messages,
            "stream": False,
            "keep_alive": cfg.get("keep_alive", "10m"),
            "options": ollama_options(num_ctx, max_tokens, temperature),
        }
        if schema is not None:
            body["format"] = schema
        t0 = time.time()
        timeout = float((self.config.get("limits") or {}).get("request_timeout_s", 300))
        r = httpx.post(f"{cfg.get('url', 'http://127.0.0.1:11434')}/api/chat", json=body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        return LLMResult(
            text=(data.get("message") or {}).get("content", ""),
            model=f"ollama/{name}",
            input_tokens=int(data.get("prompt_eval_count") or prompt_tokens),
            output_tokens=int(data.get("eval_count") or 0),
            finish_reason="length" if data.get("done_reason") == "length" else "stop",
            latency_s=time.time() - t0,
        )

    def _openai(self, name: str, messages: list[dict], schema: dict | None, max_tokens: int, temperature: float, on_text=None) -> LLMResult:
        """OpenAI through its own SDK. Reasoning models take no temperature and count tokens as completion tokens."""
        from .providers import ProviderError, is_reasoning, openai_client

        try:
            client = openai_client(timeout=float((self.config.get("limits") or {}).get("request_timeout_s", 300)))
        except ProviderError as exc:
            raise LLMError(str(exc)) from exc
        kwargs: dict[str, Any] = {"model": name, "messages": messages}
        if is_reasoning(name):
            kwargs["max_completion_tokens"] = max(max_tokens * 3, 4000)  # room for hidden reasoning
            kwargs["reasoning_effort"] = "low"
        else:
            kwargs["max_completion_tokens"] = max_tokens
            kwargs["temperature"] = temperature
        if schema is not None:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": False}}
        t0 = time.time()
        if on_text is None:
            resp = client.chat.completions.create(**kwargs)
            choice = resp.choices[0]
            text = choice.message.content or ""
            finish = str(choice.finish_reason or "stop")
            usage = resp.usage
        else:
            pieces: list[str] = []
            finish, usage = "stop", None
            for chunk in client.chat.completions.create(**kwargs, stream=True, stream_options={"include_usage": True}):
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                if chunk.choices:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        pieces.append(delta)
                        on_text("".join(pieces))
                    if chunk.choices[0].finish_reason:
                        finish = str(chunk.choices[0].finish_reason)
            text = "".join(pieces)
        result = LLMResult(
            text=text,
            model=f"openai/{name}",
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            finish_reason="length" if finish == "length" else "stop",
            latency_s=time.time() - t0,
        )
        result.cost_usd = self.price(f"openai/{name}", result.input_tokens, result.output_tokens)
        return result

    def _litellm(self, model: str, messages: list[dict], schema: dict | None, max_tokens: int, temperature: float) -> LLMResult:
        import litellm  # imported lazily: heavy import, only needed for API profiles

        kwargs: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if schema is not None:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}}
        t0 = time.time()
        resp = litellm.completion(**kwargs)
        choice = resp.choices[0]
        usage = getattr(resp, "usage", None) or {}
        cost = 0.0
        try:
            cost = float(litellm.completion_cost(completion_response=resp) or 0.0)
        except Exception:
            pass
        res = LLMResult(
            text=choice.message.content or "",
            model=model,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            finish_reason=str(choice.finish_reason or "stop"),
            latency_s=time.time() - t0,
            cost_usd=cost,
        )
        store.event(self.run_id, "cost", {"model": model, "usd": cost})
        return res


def _apply_provider_keys() -> None:
    """Copy stored provider keys into the process environment for this call."""
    mapping = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
    try:
        from . import secrets
    except Exception:
        return
    for name, env in mapping.items():
        if os.environ.get(env):
            continue
        try:
            found = secrets.value(name)
        except Exception:
            continue
        if found:
            os.environ[env] = found


def profile_report(config: dict | None = None, keys: list[dict] | None = None) -> list[str]:
    """Describe the three profiles. Key values are never included."""
    cfg = config if config is not None else load_config()
    if keys is None:
        from . import secrets

        try:
            keys = secrets.status()
        except Exception:
            keys = []
    stored = {row.get("name") for row in keys if row.get("set")}
    lines = [f"active {cfg.get('active_profile') or 'dev'}"]
    for name, roles in (cfg.get("profiles") or {}).items():
        lines.append(name)
        for role, model in (roles or {}).items():
            lines.append(f"  {role} {model}")
        if name == "dev":
            lines.append("  work unit 4 blocks, sequential")
        else:
            needed = "anthropic" if name == "claude" else "openai" if name == "openai" else ""
            if needed and needed not in stored:
                lines.append("  real use waits until a key is stored")
            elif needed:
                lines.append("  key stored")
    return lines


def pretty_model(name: str) -> str:
    """qwen2.5-coder:7b reads as Qwen2.5 Coder 7B, claude-sonnet-4-5 as Claude Sonnet 4.5."""
    short = str(name).split("/", 1)[-1]
    words: list[str] = []
    for token in (part for part in re.split(r"[-:_\s]+", short) if part):
        if words and token.isdigit() and re.fullmatch(r"\d+(?:\.\d+)*", words[-1]):
            words[-1] += "." + token
            continue
        low = token.lower()
        if low == "gpt":
            words.append("GPT")
        elif re.fullmatch(r"\d+b", low):
            words.append(low[:-1] + "B")
        elif token[0].isalpha():
            words.append(token[0].upper() + token[1:])
        else:
            words.append(token)
    return " ".join(words)


def parse_json(text: str) -> Any:
    """Parse model JSON, tolerating code fences and leading/trailing prose."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    start = min([i for i in (t.find("{"), t.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON object found")
    end = max(t.rfind("}"), t.rfind("]"))
    try:
        return json.loads(t[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(str(exc)) from exc
