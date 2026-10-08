import re

from spark_agent.draft import _DEPTHS, choose_depth, clean_outline, clean_section, section_band
from spark_agent.revise import revise
from spark_docs.parser import claude_diagram_spec

BRIEF = "Write a client proposal for ACME Fleet about fuel tracking on 340 vans"
FLOW = """---
title: Flow
doc_type: DOCUMENTATION
include_spark_content: false
---

# Flow

## Approach

```mermaid
flowchart TD
  A[Start] --> B[End]
```
"""


def _source(markdown: str) -> str:
    found = re.search(r"```(?:mermaid|diagram)\n(.*?)```", markdown, re.S)
    assert found
    return found.group(1)


def test_a_rich_brief_becomes_standard_not_short():
    rich = "\n".join(
        [
            "Write a technical document for Condor Energy driver verification.",
            "- Paper handover today",
            "- Card tap at ignition",
            "- IMG GPS integration",
            "- 28 data items from the client",
            "- Face check as optional pilot",
        ]
    )
    assert choose_depth(rich, "short") == "standard"
    band = section_band(_DEPTHS["standard"], {"heading": "Scope", "points": ["a", "b", "c", "d"], "pieces": ["prose", "table", "diagram"]})
    assert band["high"] > _DEPTHS["standard"]["high"]


def test_a_proposal_is_standard_and_a_note_stays_short():
    assert choose_depth(BRIEF, "full") == "standard"
    assert choose_depth("Status note on the yard.", "full") == "short"
    assert choose_depth("Write a technical design for the fuel tracking architecture.", "") == "full"
    plan = clean_outline(
        {"title": "Fuel", "sections": [{"heading": "Scope of Work", "points": ["vans"]}, {"heading": "Approach", "points": ["flow"]}]},
        BRIEF,
    )
    assert plan["depth"] == "standard"
    scope = plan["sections"][0]
    approach = plan["sections"][1]
    assert "table" in scope["pieces"] and "diagram" not in scope["pieces"]
    assert "diagram" in approach["pieces"]


def test_a_broken_diagram_is_repaired():
    body = clean_section("The flow.\n\n```mermaid\nIntake -> Review -> Report\n```\n", "Approach")
    source = _source(body)
    assert "Intake" in source and "Report" in source
    assert claude_diagram_spec(source, "mermaid")
    kept = clean_section("```mermaid\nflowchart LR\n  A[Start] --> B[End]\n```", "Approach")
    assert "A[Start]" in kept and "B[End]" in kept


def test_a_diagram_can_be_renamed_and_redrawn():
    renamed = revise(FLOW, "rename Start to Intake on the diagram")
    assert renamed.markdown and "Intake" in renamed.markdown
    assert renamed.summary.startswith("Updated the diagram")

    class Router:
        def complete(self, role, system, user, schema=None, max_tokens=None, temperature=0.2):
            class Result:
                text = "```mermaid\nflowchart LR\n  A[Intake] --> B[Report]\n```"

            return Result()

    redrawn = revise(FLOW, "redraw the diagram so it ends at Report", Router())
    assert redrawn.markdown and "Report" in _source(redrawn.markdown)
    assert claude_diagram_spec(_source(redrawn.markdown), "mermaid")
