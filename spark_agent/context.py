"""What the agent sees each turn.

The prompt is laid out so the stable part comes first and stays cacheable:

1. system: who Docufy is, the workflow, the template guide, the chat components
2. history: a rolling summary of older turns, then recent turns verbatim, newest last
3. this turn: the message, any attachments, and a snapshot of the workspace (drafts,
   documents, the open document, pinned notes)

History is fitted to a token budget from the newest turn back. When older turns fall
out of the budget they are folded into the summary by the fast model, so a long chat
keeps its decisions without resending every word.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import store, workspace
from .tokens import estimate_tokens

HISTORY_TOKENS = 36_000
TURN_CAP = 7_000
INLINE_ATTACHMENT = 24_000
_OPENUI = re.compile(r"```openui-lang\s*\n(.*?)```", re.S)
_IMAGE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
SUMMARY_PROMPT = (
    "You keep the running memory of a document-writing chat. Merge the earlier summary with the new turns into "
    "one summary of at most 350 words. Keep: who the client is, what documents and drafts exist and their ids, "
    "decisions made, facts and figures the person gave, open questions, and preferences on tone and layout. "
    "Drop greetings and anything superseded. Write plain bullet points."
)


@dataclass
class Context:
    history: list[dict]
    user: list[dict]
    attachments: list[dict] = field(default_factory=list)
    summary: str = ""
    dropped: int = 0


def build(chat_id: str, assistant_id: str, user_id: str, body: str, *, target_doc: str, studio_dir: Path, summarize=None) -> Context:
    row = store.conn().execute("SELECT created_at FROM messages WHERE id=?", (user_id,)).fetchone()
    before = float(row[0]) if row else 9e18
    turns = _turns(chat_id, before)
    summary_text, upto = workspace.summary(chat_id)
    kept: list[dict] = []
    used = estimate_tokens(summary_text)
    for turn in reversed(turns):
        if turn["created_at"] <= upto:
            break
        cost = estimate_tokens(turn["content"])
        if used + cost > HISTORY_TOKENS and kept:
            break
        kept.append(turn)
        used += cost
    kept.reverse()
    overflow = [turn for turn in turns if upto < turn["created_at"] < (kept[0]["created_at"] if kept else before)]
    if overflow and summarize is not None:
        transcript = "\n\n".join(f"{turn['role'].upper()}: {turn['content'][:3000]}" for turn in overflow)
        try:
            merged = summarize(SUMMARY_PROMPT, f"Earlier summary:\n{summary_text or '(none)'}\n\nNew turns:\n{transcript}")
        except Exception:
            merged = ""
        if merged.strip():
            summary_text = merged.strip()
            workspace.save_summary(chat_id, summary_text, overflow[-1]["created_at"])
    history: list[dict] = []
    if summary_text:
        history.append({"role": "user", "content": f"<earlier_conversation_summary>\n{summary_text}\n</earlier_conversation_summary>"})
        history.append({"role": "assistant", "content": "Noted. I have the earlier context."})
    for turn in kept:
        history.append({"role": turn["role"], "content": turn["content"]})
    history = _alternate(history)
    attachments = chat_attachments(chat_id, studio_dir)
    current = [item for item in attachments if item["message_id"] == user_id]
    parts: list[dict] = [{"type": "input_text", "text": body}]
    for item in current:
        if item["image"]:
            parts.append({"type": "input_image", "image_url": item["image"]})
        elif estimate_tokens(item["text"]) <= INLINE_ATTACHMENT:
            parts.append({"type": "input_text", "text": f"<attachment name=\"{item['name']}\">\n{item['text']}\n</attachment>"})
        else:
            parts.append(
                {
                    "type": "input_text",
                    "text": f"<attachment name=\"{item['name']}\" characters=\"{len(item['text'])}\">Too long to include. "
                    f"Read it with read_attachment. It starts:\n{item['text'][:4000]}\n</attachment>",
                }
            )
    parts.append({"type": "input_text", "text": snapshot(chat_id, target_doc, attachments)})
    return Context(history=history, user=parts, attachments=[a for a in attachments if not a["image"]], summary=summary_text, dropped=len(overflow))


def _turns(chat_id: str, before: float) -> list[dict]:
    rows = store.conn().execute(
        "SELECT id, role, body, status, document_id, attachment, activity, created_at FROM messages "
        "WHERE chat_id=? AND created_at<? ORDER BY created_at",
        (chat_id, before),
    ).fetchall()
    titles = {r[0]: (r[1], r[2]) for r in store.conn().execute("SELECT id, title, version FROM documents WHERE chat_id=?", (chat_id,))}
    turns = []
    for message_id, role, body, status, document_id, attachment, activity, created_at in rows:
        if role == "assistant" and status not in {"ready", "stopped"}:
            continue
        text = str(body or "").strip()
        if role == "assistant":
            text = _OPENUI.sub(lambda m: "[visual: " + _openui_gist(m.group(1)) + "]", text)
            tools = _tool_line(activity)
            if tools:
                text = f"{text}\n\n({tools})"
            if document_id and document_id in titles:
                title, version = titles[document_id]
                text += f"\n(Published document {document_id}: {title} v{version or 1})"
            if status == "stopped":
                text += "\n(This reply was stopped by the person.)"
        elif attachment:
            text = f"{text}\n(Attached file: {attachment})"
        if not text:
            continue
        if len(text) > TURN_CAP * 4:
            text = text[: TURN_CAP * 2] + "\n[...]\n" + text[-TURN_CAP * 2 :]
        turns.append({"id": message_id, "role": role, "content": text, "created_at": float(created_at)})
    return turns


def _tool_line(raw: str | None) -> str:
    try:
        steps = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return ""
    names = [f"{step.get('label')}{': ' + step['detail'] if step.get('detail') else ''}" for step in steps if step.get("kind") == "tool"]
    return ("tools used: " + "; ".join(names[:12])) if names else ""


def _openui_gist(code: str) -> str:
    strings = re.findall(r'"([^"]{8,})"', code)
    return " / ".join(strings[:6])[:600] or "components"


def _alternate(items: list[dict]) -> list[dict]:
    """Join same-role neighbours so every provider accepts the order."""
    out: list[dict] = []
    for item in items:
        if out and out[-1]["role"] == item["role"]:
            out[-1] = {"role": item["role"], "content": out[-1]["content"] + "\n\n" + item["content"]}
        else:
            out.append(dict(item))
    return out


def chat_attachments(chat_id: str, studio_dir: Path) -> list[dict]:
    rows = store.conn().execute(
        "SELECT id, attachment FROM messages WHERE chat_id=? AND role='user' AND attachment!='' ORDER BY created_at",
        (chat_id,),
    ).fetchall()
    out = []
    for message_id, name in rows:
        folder = studio_dir / "uploads" / message_id
        path = folder / name
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in _IMAGE:
            data = path.read_bytes()
            if len(data) > 8 * 1024 * 1024:
                continue
            url = f"data:{_IMAGE[suffix]};base64,{base64.b64encode(data).decode()}"
            out.append({"message_id": message_id, "name": name, "text": "", "image": url})
            continue
        try:
            text = workspace.attachment_text(folder, name)
        except Exception as exc:  # noqa: BLE001 - one unreadable upload must not stop the turn
            text = f"(this file could not be read: {exc})"
        out.append({"message_id": message_id, "name": name, "text": text, "image": ""})
    return out


def snapshot(chat_id: str, target_doc: str, attachments: list[dict]) -> str:
    """A compact picture of the chat's workspace, rebuilt every turn."""
    from .llm import now_line

    lines = ["<workspace>", now_line().split(". ")[0] + "."]
    drafts = workspace.drafts_for_chat(chat_id)
    if drafts:
        lines.append("Drafts (canvas):")
        for draft in drafts:
            linked = " published" if draft["document_root"] else " not published yet"
            lines.append(f"- {draft['id']} \"{draft['title']}\" v{draft['version']}, {draft['words']} words,{linked}")
            outline = [item.strip() for item in draft["outline"][:14]]
            if outline:
                lines.append("  outline: " + " | ".join(outline))
    else:
        lines.append("Drafts: none yet.")
    docs = store.conn().execute(
        "SELECT d.id, d.title, d.version, d.pages, d.spark_content, COALESCE(NULLIF(d.root_id,''), d.id) FROM documents d "
        "WHERE d.chat_id=? ORDER BY d.created_at",
        (chat_id,),
    ).fetchall()
    latest: dict[str, tuple] = {}
    for row in docs:
        if row[5] not in latest or (row[2] or 1) > (latest[row[5]][2] or 1):
            latest[row[5]] = row
    if latest:
        lines.append("Published documents (Spark template):")
        for row in latest.values():
            lines.append(f"- {row[0]} \"{row[1]}\" v{row[2] or 1}, {row[3]} pages, company pages {'on' if row[4] else 'off'}")
    if target_doc:
        open_row = next((row for row in docs if row[0] == target_doc), None)
        if open_row:
            lines.append(f"Open in the preview: {open_row[0]} \"{open_row[1]}\" v{open_row[2] or 1}. 'This document' means this one.")
    pinned = workspace.notes(chat_id)
    if pinned:
        lines.append("Pinned notes:")
        lines.extend(f"- [{item['id']}] {item['text']}" for item in pinned)
    files = [item["name"] for item in attachments]
    if files:
        lines.append("Attachments in this chat: " + ", ".join(files))
    lines.append("</workspace>")
    return "\n".join(lines)
