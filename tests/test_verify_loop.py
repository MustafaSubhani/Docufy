"""Verify loop: hard checks, bbox-required critic, fixer ladder, and stop rules."""

import json

from spark_agent.llm import LLMError, Router
from spark_agent.loop import accept_soft, hard_checks, run_loop

DOC = """---
title: Fleet
doc_type: DOCUMENT
---

# 01 Overview

## Overview

The fleet is organized and ready for 340 vehicles.
"""


def _quiet(model, messages, schema):
    user = messages[-1]["content"]
    if "Verification critic" in user:
        return json.dumps({"issues": []})
    return "ok"


def test_lexicon_is_fixed_and_kept():
    router = Router(profile="dev", fake=_critic_then_revert)
    res = run_loop(DOC, router, log=lambda *_: None)
    assert "organised" in res.markdown
    assert "organized" not in res.markdown
    assert res.status == "reintroduced"
    assert res.score == 95


def test_critic_issue_without_a_box_is_dropped():
    kept = accept_soft(
        {
            "issues": [
                {"message": "loose", "severity": "soft", "fix_type": "patch"},
                {"message": "tighten", "page": 1, "bbox": [60, 400, 400, 40], "section": "Overview", "fix_type": "patch"},
            ]
        }
    )
    assert len(kept) == 1
    assert kept[0].bbox == [60, 400, 400, 40]


def test_numbering_and_cards_are_auto_fixed():
    md = """---
title: Fleet
doc_type: DOCUMENT
---

# 01 Overview

## Overview

Ready.

# 04 Next

## Next

Done.

:::cards
### One

Alpha

### Two

Beta
:::
"""
    layout = {"pages": 1, "blocks": [{"id": "b9", "kind": "cards", "page": 1, "x": -8, "y": 100, "w": 200, "h": 40}]}
    router = Router(profile="dev", fake=_quiet)
    res = run_loop(md, router, layout=layout, log=lambda *_: None)
    assert res.status == "clean"
    assert "# 02 Next" in res.markdown
    assert ":::cards" not in res.markdown
    assert "- **One.**" in res.markdown


def test_page_budget_escalates():
    router = Router(profile="dev", fake=_quiet)
    clean = DOC.replace("organized", "organised")
    res = run_loop(clean, router, archetype="client_guide", pages=20, log=lambda *_: None)
    assert res.status == "escalated"
    assert any(item.code == "page_budget" for item in res.issues)


def test_plateau_keeps_the_original_when_the_fix_does_not_help():
    router = Router(profile="dev", fake=_always_patch)
    router.config["budgets"] = {"max_verify_iterations": 4, "score_delta": 1}
    clean = DOC.replace("organized", "organised")
    res = run_loop(clean, router, log=lambda *_: None)
    assert res.status == "plateau"
    assert "Noted." not in res.markdown


def test_iteration_cap():
    router = Router(profile="dev", fake=_always_patch)
    router.config["budgets"] = {"max_verify_iterations": 2, "score_delta": 0}
    clean = DOC.replace("organized", "organised")
    res = run_loop(clean, router, log=lambda *_: None)
    assert res.status == "capped"
    assert res.iterations == 2


def test_budget_stops_on_a_patch():
    router = Router(profile="dev", fake=_budget_on_write)
    router.config["budgets"] = {"max_verify_iterations": 4, "score_delta": 1, "per_run_usd": 3}
    clean = DOC.replace("organized", "organised")
    res = run_loop(clean, router, log=lambda *_: None)
    assert res.status == "budget"


def test_invented_number_is_a_hard_issue():
    md = DOC.replace("organized", "organised").replace("340", "340 and 99")
    issues = hard_checks(md, source_body="The fleet is ready for 340 vehicles.")
    assert any(i.code == "numbers" and "99" in i.message for i in issues)


def test_overflow_and_low_heading_carry_a_box():
    layout = {
        "blocks": [
            {"id": "b1", "kind": "kicker", "page": 2, "x": 60, "y": 10, "w": 120, "h": 14},
            {"id": "b2", "kind": "para", "page": 2, "x": 700, "y": 400, "w": 50, "h": 20},
        ]
    }
    issues = hard_checks(DOC.replace("organized", "organised"), layout=layout)
    boxes = {i.code: i.bbox for i in issues}
    assert boxes["heading_low"] == [60, 10, 120, 14]
    assert boxes["overflow"][0] == 700


def _critic_then_revert(model, messages, schema):
    user = messages[-1]["content"]
    if "Verification critic" in user:
        if "organised" in user:
            return json.dumps(
                {
                    "issues": [
                        {
                            "message": "shorten the overview",
                            "page": 1,
                            "block_id": "b1",
                            "section": "Overview",
                            "bbox": [60, 400, 400, 40],
                            "severity": "soft",
                            "fix_type": "patch",
                        }
                    ]
                }
            )
        return json.dumps({"issues": [{"message": "no box", "severity": "soft", "fix_type": "patch"}]})
    if user.startswith("ISSUE:"):
        return user.split("SECTION:\n", 1)[1].replace("organised", "organized")
    return "ok"


def _always_patch(model, messages, schema):
    user = messages[-1]["content"]
    if "Verification critic" in user:
        return json.dumps(
            {
                "issues": [
                    {
                        "message": "tighten",
                        "page": 1,
                        "section": "Overview",
                        "bbox": [60, 400, 400, 30],
                        "severity": "soft",
                        "fix_type": "patch",
                    }
                ]
            }
        )
    if user.startswith("ISSUE:"):
        return user.split("SECTION:\n", 1)[1].rstrip() + " Noted.\n"
    return "ok"


def _budget_on_write(model, messages, schema):
    user = messages[-1]["content"]
    if "Verification critic" in user:
        return json.dumps(
            {
                "issues": [
                    {
                        "message": "tighten",
                        "page": 1,
                        "section": "Overview",
                        "bbox": [60, 400, 400, 30],
                        "severity": "soft",
                        "fix_type": "patch",
                    }
                ]
            }
        )
    raise LLMError("run budget $1.00 reached ($5.00 spent)")
