"""Bounded work queue. Local profiles stay on one worker. Cloud profiles share a small pool.

Caps, a memory pause, and per-item timeouts keep a long run from loading the machine.
Fetched pages can be written to disk so only a short passage stays in memory.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CAPS = {
    "workers": 4,
    "llm": 4,
    "fetch": 8,
    "domain": 2,
    "browser": 2,
    "cpu": 2,
    "gpu": 1,
}
LOW_CAPS = {
    "workers": 2,
    "llm": 1,
    "fetch": 2,
    "domain": 1,
    "browser": 1,
    "cpu": 1,
    "gpu": 1,
}
_YEAR = re.compile(r"^(?:19|20)\d\d$")
_QUANTITY = re.compile(r"\b(\d[\d,]*)\s+([A-Za-z][A-Za-z-]{2,})\b")
_EM = "\u2014"
_WAIT = object()
_DONE = object()


class PressureError(RuntimeError):
    """A lane should back off: the provider returned 429, or the call was too slow."""


class Lane:
    def __init__(self, name: str, capacity: int):
        self.name = name
        self.capacity = max(1, int(capacity))
        self._slots = threading.Semaphore(self.capacity)

    @contextmanager
    def acquire(self):
        self._slots.acquire()
        try:
            yield
        finally:
            self._slots.release()


class Budget:
    """One lane's in-flight limit. A 429 halves it. A streak of successes gives a slot back."""

    def __init__(self, name: str, capacity: int, streak: int = 3):
        self.name = name
        self.capacity = max(1, int(capacity))
        self.limit = self.capacity
        self.streak = max(1, int(streak))
        self.inflight = 0
        self.successes = 0
        self._cv = threading.Condition()

    def acquire(self, stop: threading.Event, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._cv:
            while self.inflight >= self.limit and not stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cv.wait(min(remaining, 0.05))
            if stop.is_set() or self.inflight >= self.limit:
                return False
            self.inflight += 1
            return True

    def release(self, ok: bool, pressure: bool = False) -> None:
        with self._cv:
            self.inflight = max(0, self.inflight - 1)
            if pressure:
                self.limit = max(1, self.limit // 2)
                self.successes = 0
            if ok:
                self.successes += 1
                if self.successes >= self.streak and self.limit < self.capacity:
                    self.limit += 1
                    self.successes = 0
            elif not pressure:
                self.successes = 0
            self._cv.notify_all()


@dataclass
class ItemResult:
    index: int
    ok: bool
    value: object = None
    error: str = ""
    attempts: int = 0
    paused: bool = False


@dataclass
class RunReport:
    results: list[ItemResult]
    paused: bool
    trimmed: bool
    max_in_flight: int
    lane_limit: int
    notes: list[str] = field(default_factory=list)


def _local(router) -> bool:
    try:
        model = router.model_for("planner")
    except Exception:
        model = ""
    return router.profile == "dev" or str(model).startswith("ollama/")


def workers_for(router) -> int:
    """How many section planners may run at once. Local profiles stay at 1."""
    gov = router.config.get("governor") or {}
    if _local(router):
        count = max(1, int(gov.get("dev_workers", 1)))
    else:
        count = max(1, int(gov.get("cloud_workers", 4)))
    if gov.get("low_resource"):
        count = 1 if _local(router) else min(count, 2)
    return count


def caps_for(router=None, *, low_resource: bool = False, local: bool = False) -> dict:
    gov = (router.config.get("governor") or {}) if router is not None else {}
    low_resource = low_resource or bool(gov.get("low_resource"))
    if router is not None:
        local = local or _local(router)
    table = {
        "workers": int(gov.get("cloud_workers", DEFAULT_CAPS["workers"])),
        "llm": int(gov.get("llm_per_provider", DEFAULT_CAPS["llm"])),
        "fetch": int(gov.get("fetches", DEFAULT_CAPS["fetch"])),
        "domain": int(gov.get("fetches_per_domain", DEFAULT_CAPS["domain"])),
        "browser": int(gov.get("browser", DEFAULT_CAPS["browser"])),
        "cpu": int(gov.get("cpu", DEFAULT_CAPS["cpu"])),
        "gpu": int(gov.get("gpu", DEFAULT_CAPS["gpu"])),
    }
    if low_resource:
        for key, limit in LOW_CAPS.items():
            table[key] = min(table[key], limit)
    if local:
        table["workers"] = 1 if low_resource else max(1, int(gov.get("dev_workers", 1)))
        table["gpu"] = 1
    return table


def system_memory_percent() -> float:
    try:
        import psutil
    except Exception:
        return 0.0
    try:
        return float(psutil.virtual_memory().percent)
    except Exception:
        return 0.0


def describe(router) -> list[str]:
    table = caps_for(router)
    local = _local(router)
    mode = "sequential" if local else "parallel"
    low = "on" if (router.config.get("governor") or {}).get("low_resource") else "off"
    return [
        f"profile {router.profile}",
        f"mode {mode}",
        f"workers {workers_for(router)}",
        f"low resource {low}",
        f"llm per provider {table['llm']}",
        f"fetches {table['fetch']} ({table['domain']} per domain)",
        f"browser {table['browser']}",
        f"cpu {table['cpu']}",
        f"gpu {table['gpu']}",
    ]


def spill_page(cache_dir: Path, url: str, text: str, passage_chars: int = 480) -> dict:
    """Write a fetched page to disk and keep only a short passage in the result."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    path = cache_dir / f"{name}.txt"
    path.write_text(text, encoding="utf-8")
    passage = text[:passage_chars].strip()
    return {"url": url, "path": str(path), "passage": passage, "chars": len(text)}


def harmonize(sections: list[dict], glossary: list[str] | None = None) -> list[str]:
    """Flag glossary forms and quantities that disagree across drafted sections."""
    notes: list[str] = []
    quantities: dict[str, dict[str, str]] = {}
    dashed: list[str] = []
    for section in sections:
        name = str(section.get("name") or section.get("id") or "section")
        text = str(section.get("text") or "")
        if _EM in text:
            dashed.append(name)
        for match in _QUANTITY.finditer(text):
            number, noun = match.group(1), match.group(2).lower()
            if _YEAR.match(number):
                continue
            quantities.setdefault(noun, {})[name] = number
    for noun, found in quantities.items():
        if len(set(found.values())) > 1:
            detail = ", ".join(f"{name} says {value}" for name, value in found.items())
            notes.append(f"{noun} disagrees across sections: {detail}")
    if dashed and len(dashed) != len(sections):
        notes.append("em dash appears in " + ", ".join(dashed))
    for term in glossary or []:
        forms: dict[str, list[str]] = {}
        for section in sections:
            name = str(section.get("name") or "section")
            text = str(section.get("text") or "")
            for match in re.finditer(re.escape(term), text, re.IGNORECASE):
                forms.setdefault(match.group(0), []).append(name)
        if len(forms) > 1:
            notes.append(term + " is written in more than one way: " + ", ".join(forms))
    return notes


def run_items(
    items,
    fn,
    *,
    workers: int = 4,
    lane: str = "cpu",
    provider: str = "",
    domain_of=None,
    caps: dict | None = None,
    memory_percent=None,
    gpu_percent=None,
    on_trim=None,
    on_checkpoint=None,
    timeout_s: float = 30,
    retries: int = 1,
    low_resource: bool = False,
    success_streak: int = 3,
    pause_at: float = 75,
    trim_at: float = 85,
    checkpoint_at: float = 92,
    run_id: str | None = None,
) -> RunReport:
    """Run work items on a small pool. One failure, timeout, or memory pause stays inside the run."""
    if not items:
        return RunReport([], False, False, 0, 0)
    table = dict(DEFAULT_CAPS)
    if low_resource:
        for key, limit in LOW_CAPS.items():
            table[key] = min(table[key], limit)
    if caps:
        table.update(caps)
        if low_resource:
            for key, limit in LOW_CAPS.items():
                if key in table:
                    table[key] = min(int(table[key]), limit)
    pool = max(1, min(int(workers), int(table["workers"]), len(items)))
    memory = memory_percent or system_memory_percent
    gpu = gpu_percent or (lambda: 0.0)
    stop = threading.Event()
    state_lock = threading.Lock()
    pending = list(range(len(items)))
    results: list[ItemResult | None] = [None] * len(items)
    pause_flag = {"paused": False, "trimmed": False, "trimming": False}
    checkpoint_box: dict = {}
    inflight = {"n": 0, "max": 0}
    budgets: dict[str, Budget] = {}
    primary_name = _lane_name(lane, provider)

    def budget_for(name: str, capacity: int) -> Budget:
        if name not in budgets:
            budgets[name] = Budget(name, capacity, success_streak)
        return budgets[name]

    def level() -> float:
        try:
            ram = float(memory() or 0)
        except Exception:
            ram = 0.0
        try:
            card = float(gpu() or 0)
        except Exception:
            card = 0.0
        return max(ram, card)

    def emit() -> None:
        with state_lock:
            if checkpoint_box.get("sent") or "payload" not in checkpoint_box:
                payload = None
            else:
                checkpoint_box["sent"] = True
                payload = checkpoint_box["payload"]
        if not payload:
            return
        if on_checkpoint:
            on_checkpoint(payload)
        if run_id:
            from . import store

            store.save_checkpoint(run_id, "paused", payload)
            store.set_status(run_id, "paused")

    def pause(reason: str, current: float) -> None:
        if pause_flag["paused"]:
            return
        pause_flag["paused"] = True
        stop.set()
        leftover = list(pending)
        pending.clear()
        checkpoint_box["payload"] = {"reason": reason, "pending": leftover, "level": current}
        for index in leftover:
            results[index] = ItemResult(index=index, ok=False, error="paused", paused=True)

    def take():
        while True:
            do_trim = False
            current = 0.0
            with state_lock:
                if stop.is_set() or not pending:
                    return _DONE
                current = level()
                if current >= checkpoint_at:
                    pause("checkpoint", current)
                    return _DONE
                if current >= trim_at and not pause_flag["trimmed"]:
                    pause_flag["trimmed"] = True
                    pause_flag["trimming"] = True
                    do_trim = True
            if do_trim:
                try:
                    if on_trim:
                        on_trim({"level": current})
                finally:
                    with state_lock:
                        pause_flag["trimming"] = False
                continue
            with state_lock:
                if stop.is_set() or not pending:
                    return _DONE
                current = level()
                if current >= checkpoint_at:
                    pause("checkpoint", current)
                    return _DONE
                if current >= pause_at:
                    if inflight["n"] == 0 and not pause_flag["trimming"]:
                        pause("pause", current)
                        return _DONE
                    return _WAIT
                return pending.pop(0)

    def lane_budgets(item):
        chosen, prov, host = _meta(item, lane, provider, domain_of)
        name = _lane_name(chosen, prov)
        capacity = _lane_capacity(chosen, table)
        main = budget_for(name, capacity)
        extra = None
        if chosen == "fetch" and host:
            extra = budget_for(f"domain:{host}", int(table["domain"]))
        return main, extra

    def worker() -> None:
        while True:
            got = take()
            if got is _DONE:
                emit()
                return
            if got is _WAIT:
                time.sleep(0.02)
                continue
            index = got
            item = items[index]
            main, extra = lane_budgets(item)
            if not main.acquire(stop, max(timeout_s, 1)):
                _settle(results, index, stop, pause_flag, state_lock)
                continue
            if extra is not None and not extra.acquire(stop, max(timeout_s, 1)):
                main.release(False)
                _settle(results, index, stop, pause_flag, state_lock)
                continue
            with state_lock:
                inflight["n"] += 1
                inflight["max"] = max(inflight["max"], inflight["n"])
            result = ItemResult(index=index, ok=False, error="failed")
            pressure = False
            try:
                result, pressure = _attempt(index, item, fn, timeout_s, retries)
            finally:
                with state_lock:
                    inflight["n"] = max(0, inflight["n"] - 1)
                if extra is not None:
                    extra.release(result.ok, pressure)
                main.release(result.ok, pressure)
            results[index] = result

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(pool)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    emit()
    for index, result in enumerate(results):
        if result is None:
            results[index] = ItemResult(index=index, ok=False, error="paused", paused=pause_flag["paused"])
    primary = budgets.get(primary_name)
    return RunReport(
        results=results,
        paused=pause_flag["paused"],
        trimmed=pause_flag["trimmed"],
        max_in_flight=inflight["max"],
        lane_limit=primary.limit if primary else int(table.get("workers", 1)),
    )


def _lane_name(lane: str, provider: str) -> str:
    if lane == "cloud":
        return f"cloud:{provider or 'default'}"
    return lane


def _lane_capacity(lane: str, table: dict) -> int:
    if lane == "cloud":
        return int(table["llm"])
    if lane == "fetch":
        return int(table["fetch"])
    if lane == "browser":
        return int(table["browser"])
    if lane == "gpu":
        return int(table["gpu"])
    return int(table["cpu"])


def _meta(item, lane: str, provider: str, domain_of):
    chosen, prov, host = lane, provider, ""
    if isinstance(item, dict):
        chosen = item.get("lane") or lane
        prov = item.get("provider") or provider
        host = item.get("domain") or ""
    if domain_of is not None:
        host = domain_of(item) or host
    return chosen, prov, host


def _settle(results, index, stop, pause_flag, state_lock) -> None:
    with state_lock:
        paused = stop.is_set() or pause_flag["paused"]
    results[index] = ItemResult(index=index, ok=False, error="paused" if paused else "timeout", paused=paused)


def _attempt(index: int, item, fn, timeout_s: float, retries: int) -> tuple[ItemResult, bool]:
    attempts = 0
    pressure = False
    result = ItemResult(index=index, ok=False, error="failed")
    while True:
        attempts += 1
        result, kind = _call(index, item, fn, timeout_s)
        result.attempts = attempts
        if kind == "pressure":
            pressure = True
        if kind == "ok":
            return result, pressure
        if kind == "timeout" or attempts > retries:
            return result, pressure or kind == "timeout"


def _call(index: int, item, fn, timeout_s: float) -> tuple[ItemResult, str]:
    box: dict = {}

    def target() -> None:
        try:
            box["value"] = fn(item)
        except PressureError as exc:
            box["pressure"] = str(exc) or "429"
        except Exception as exc:
            box["error"] = str(exc) or exc.__class__.__name__

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        return ItemResult(index=index, ok=False, error="timeout"), "timeout"
    if "value" in box:
        return ItemResult(index=index, ok=True, value=box["value"]), "ok"
    if "pressure" in box:
        return ItemResult(index=index, ok=False, error=f"429 {box['pressure']}".strip()), "pressure"
    return ItemResult(index=index, ok=False, error=box.get("error") or "failed"), "error"
