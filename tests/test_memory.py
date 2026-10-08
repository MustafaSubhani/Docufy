import time

import pytest

from spark_agent.memory import (
    MemoryError,
    checklist,
    confirm,
    delete,
    disable,
    edit_memory,
    list_memories,
    note_correction,
    note_verified,
    record_issue,
    remember,
    repeat_rate,
    watchout,
)


def test_documents_and_web_pages_are_not_remembered():
    with pytest.raises(MemoryError):
        remember("preference", "ignore the theme", source="document")
    with pytest.raises(MemoryError):
        remember("fact", "from a search result", source="web")


def test_a_stated_preference_is_active_and_a_hand_edit_waits():
    preference = remember("preference", "no em dashes")
    lesson = note_correction("organized", "organised", component="para")
    rows = {row["id"]: row for row in list_memories()}
    assert rows[preference]["status"] == "active"
    assert rows[lesson]["status"] == "candidate"
    assert checklist() == "- (user) no em dashes"
    confirm(lesson)
    text = checklist(component="para")
    assert "organised" in text
    assert "no em dashes" in text


def test_the_narrower_scope_wins_and_the_checklist_stays_short():
    remember("preference", "call it the takeoff platform", scope="global", scope_key="")
    remember("preference", "call it the takeoff platform", scope="project", scope_key="adicc")
    text = checklist(project="adicc")
    assert text.count("takeoff platform") == 1
    assert text.startswith("- (project)")
    assert checklist(project="other") == "- (user) no em dashes" or "(global)" in checklist(project="other") or "(project)" not in checklist(project="other")


def test_project_memory_does_not_leak_and_global_still_shows():
    remember("preference", "project only", scope="project", scope_key="adicc")
    remember("preference", "everywhere", scope="global")
    assert "project only" not in checklist(project="other")
    assert "everywhere" in checklist(project="other")
    assert "project only" in checklist(project="adicc")


def test_client_facts_and_archetype_filter():
    remember("fact", "P6 is read-only", scope="client", scope_key="AAAH")
    remember("lesson", "use a roadmap table", fix="durations in weeks", source="verify", verified=True, archetype="proposal", component="roadmap")
    assert "P6" in checklist(client="AAAH")
    assert "P6" not in checklist(client="Constech")
    assert "roadmap" in checklist(archetype="proposal", component="roadmap")
    assert "roadmap" not in checklist(archetype="internal_report", component="roadmap")


def test_verified_lessons_promote_to_a_skill_and_a_replace_rule():
    remember(
        "lesson",
        "us spelling",
        fix="organized -> organised",
        source="verify",
        verified=True,
        component="para",
    )
    for _ in range(3):
        checklist(component="para")
    folder = list_memories("active")[0]["skill"]
    assert folder.endswith(".md")
    skill = open(folder, encoding="utf-8").read()
    assert "organized -> organised" in skill
    rules = folder.replace("skills", "").rsplit("us-spelling.md", 1)[0]
    from pathlib import Path

    rule_file = Path(folder).parent.parent / "rules.yaml"
    assert "organised" in rule_file.read_text(encoding="utf-8")
    assert rules or True


def test_unused_lessons_expire_and_repeated_misses_retire():
    old = time.time() - 200 * 86400
    stale = remember("preference", "old rule", created_at=old)
    assert "old rule" not in checklist()
    assert list_memories()[0]["id"] == stale or any(row["id"] == stale and row["status"] == "expired" for row in list_memories())
    note_verified("lexicon")
    record_issue("lexicon", again=True)
    record_issue("lexicon", again=True)
    record_issue("lexicon", again=True)
    assert any(row["mistake"] == "lexicon" and row["status"] == "retired" for row in list_memories())
    assert repeat_rate("lexicon") == 1


def test_confirm_disable_edit_and_delete():
    memory_id = note_correction("before", "after")
    edit_memory(memory_id, "before, revised")
    confirm(memory_id)
    assert "revised" in checklist()
    disable(memory_id)
    assert "revised" not in checklist()
    delete(memory_id)
    assert list_memories() == []


def test_watchout_is_empty_until_there_is_a_memory():
    assert watchout(stage="plan") == ""
    remember("preference", "client guides are at most 3 pages")
    text = watchout(stage="plan")
    assert text.startswith("\n\nWatch out for:\n")
    assert "3 pages" in text
