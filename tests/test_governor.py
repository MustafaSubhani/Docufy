import threading
import time

from spark_agent.__main__ import main
from spark_agent.governor import (
    PressureError,
    caps_for,
    harmonize,
    run_items,
    spill_page,
    system_memory_percent,
    workers_for,
)
from spark_agent.llm import Router


def test_dev_stays_sequential_and_low_resource_shrinks_the_cloud_pool(capsys):
    assert workers_for(Router(profile="dev")) == 1
    assert workers_for(Router(profile="claude")) == 4
    cloud = Router(profile="claude")
    cloud.config["governor"]["low_resource"] = True
    assert workers_for(cloud) == 2
    assert caps_for(cloud)["llm"] == 1
    assert caps_for(cloud)["fetch"] == 2
    assert caps_for(cloud)["browser"] == 1
    assert main(["governor"]) == 0
    text = capsys.readouterr().out
    assert "mode sequential" in text
    assert "workers 1" in text


def test_a_provider_lane_never_exceeds_its_cap():
    current = {"n": 0, "max": 0}
    lock = threading.Lock()

    def work(item):
        with lock:
            current["n"] += 1
            current["max"] = max(current["max"], current["n"])
        time.sleep(0.04)
        with lock:
            current["n"] -= 1
        return item

    report = run_items(
        list(range(8)),
        work,
        workers=4,
        lane="cloud",
        provider="openai",
        caps={"llm": 2, "workers": 4},
        memory_percent=lambda: 0,
        timeout_s=2,
    )
    assert current["max"] <= 2
    assert report.max_in_flight <= 2
    assert [item.ok for item in report.results] == [True] * 8


def test_one_domain_keeps_two_fetches_and_the_gpu_stays_single():
    hosts = {"n": 0, "max": 0}
    lock = threading.Lock()

    def fetch(item):
        with lock:
            hosts["n"] += 1
            hosts["max"] = max(hosts["max"], hosts["n"])
        time.sleep(0.04)
        with lock:
            hosts["n"] -= 1
        return item

    fetched = run_items(
        list(range(6)),
        fetch,
        workers=4,
        lane="fetch",
        domain_of=lambda _item: "example.com",
        caps={"fetch": 8, "domain": 2, "workers": 4},
        memory_percent=lambda: 0,
        timeout_s=2,
    )
    assert hosts["max"] <= 2
    assert fetched.max_in_flight <= 2
    assert all(item.ok for item in fetched.results)

    card = {"n": 0, "max": 0}

    def local(item):
        with lock:
            card["n"] += 1
            card["max"] = max(card["max"], card["n"])
        time.sleep(0.03)
        with lock:
            card["n"] -= 1
        return item

    gpu = run_items(list(range(4)), local, workers=4, lane="gpu", caps={"gpu": 1, "workers": 4}, memory_percent=lambda: 0, timeout_s=2)
    assert card["max"] == 1
    assert gpu.max_in_flight == 1


def test_a_429_halves_the_lane_and_successes_restore_one_slot():
    def work(item):
        if item == "pressure":
            raise PressureError("slow down")
        return item

    report = run_items(
        ["pressure", "ok", "ok", "ok"],
        work,
        workers=1,
        lane="cloud",
        provider="openai",
        caps={"llm": 4, "workers": 4},
        retries=0,
        memory_percent=lambda: 0,
        timeout_s=2,
    )
    assert report.results[0].ok is False
    assert report.results[0].error.startswith("429")
    assert [item.ok for item in report.results[1:]] == [True, True, True]
    assert report.lane_limit == 3


def test_one_failure_and_one_timeout_leave_the_other_items_finished():
    def work(item):
        if item == 1:
            raise RuntimeError("nope")
        if item == 2:
            time.sleep(0.4)
        return item

    report = run_items([0, 1, 2, 3], work, workers=4, lane="cpu", caps={"cpu": 4, "workers": 4}, retries=0, memory_percent=lambda: 0, timeout_s=0.1)
    assert report.results[0].value == 0
    assert "nope" in report.results[1].error
    assert report.results[2].error == "timeout"
    assert report.results[3].value == 3
    assert report.paused is False


def test_high_memory_pauses_and_a_trim_lets_work_continue():
    saved = {}
    blocked = run_items([1, 2, 3], lambda item: item, workers=4, memory_percent=lambda: 80, on_checkpoint=lambda payload: saved.update(payload))
    assert blocked.paused
    assert saved["reason"] == "pause"
    assert all(item.paused for item in blocked.results)

    flag = {"calls": 0}

    def memory():
        return 0 if flag["calls"] else 86

    def trim(payload):
        flag["calls"] += 1
        assert payload["level"] == 86

    trimmed = run_items([1, 2], lambda item: item, workers=2, memory_percent=memory, on_trim=trim, timeout_s=2)
    assert flag["calls"] == 1
    assert trimmed.trimmed
    assert all(item.ok for item in trimmed.results)


def test_a_checkpoint_reading_does_not_start_work_or_grow_the_process():
    before = _rss()
    called = []
    saved = {}
    report = run_items(
        list(range(20)),
        lambda item: called.append(item),
        workers=4,
        memory_percent=lambda: 93,
        on_checkpoint=lambda payload: saved.update(payload),
    )
    after = _rss()
    assert called == []
    assert report.paused
    assert saved["reason"] == "checkpoint"
    assert saved["pending"] == list(range(20))
    if before and after:
        assert after - before < 50 * 1024 * 1024
    reading = system_memory_percent()
    assert 0 <= reading <= 100


def test_a_spilled_page_keeps_the_body_on_disk(tmp_path):
    body = "passage " + ("word " * 4000)
    saved = spill_page(tmp_path, "https://example.com/a", body)
    assert "text" not in saved
    assert len(saved["passage"]) <= 480
    assert saved["chars"] == len(body)
    assert _stored(saved) == body


def test_harmonize_flags_quantities_forms_and_a_lone_em_dash():
    notes = harmonize(
        [
            {"name": "Scope", "text": "The survey covers 12 sites. Spark Technologies leads it."},
            {"name": "Close", "text": "The survey covers 14 sites. spark technologies leads it. A long\u2014pause."},
        ],
        glossary=["Spark Technologies"],
    )
    assert any("sites disagrees" in note for note in notes)
    assert any("more than one way" in note for note in notes)
    assert any("em dash" in note for note in notes)
    assert harmonize([{"name": "A", "text": "12 sites"}, {"name": "B", "text": "12 sites"}]) == []


def _rss() -> int:
    try:
        import psutil
    except Exception:
        return 0
    return int(psutil.Process().memory_info().rss)


def _stored(saved: dict) -> str:
    from pathlib import Path

    return Path(saved["path"]).read_text(encoding="utf-8")
