"""Checkpointed Smart Convert graph.

Nodes are plan, approve, draft, verify, export. Approve stops the run until
resume. The dev profile drafts one section at a time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from spark_docs.brand import load_archetypes

from . import store
from .blocks import segment, split_frontmatter
from .convert import ROOT, build_plan, execute_plan
from .llm import Router
from .loop import run_loop


class GraphError(RuntimeError):
    pass


@dataclass
class GraphResult:
    status: str
    run_id: str
    plan: dict
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    markdown: str = ""
    pdf: Path | None = None
    docx: Path | None = None
    verify_status: str = ""
    verify_score: float = 0.0


def missing_slots(plan: dict) -> list[str]:
    """Required archetype slots the plan did not cover. Spark-content slots are inserted later."""
    arch = load_archetypes().get(plan.get("archetype") or "") or {}
    blob = " ".join(f"{s.get('kicker') or ''} {s.get('title') or ''}" for s in plan.get("sections") or []).lower()
    missing = []
    for slot in arch.get("slots") or []:
        if not slot.get("required") or slot.get("spark_content"):
            continue
        label = str(slot.get("kicker") or slot.get("id") or "").lower()
        if label and label not in blob:
            missing.append(slot.get("kicker") or slot.get("id"))
    return missing


def start_convert(
    source: Path,
    router: Router | None = None,
    *,
    approve: bool = False,
    spark_content: bool | None = None,
    render: bool = True,
    docx: bool = False,
    out_dir: Path | None = None,
    log=print,
) -> GraphResult:
    source = Path(source)
    raw = source.read_text(encoding="utf-8")
    meta, body = split_frontmatter(raw)
    blocks = segment(body)
    router = router or Router()
    run_id = store.start_run("convert", str(source), router.profile)
    router.run_id = run_id
    log(f"run {run_id}: plan")
    plan, issues = build_plan(router, blocks, meta, log)
    store.event(run_id, "doc_plan", plan)
    log(f"  archetype {plan.get('archetype')}, {len(plan.get('sections') or [])} sections")
    state = {
        "source": str(source),
        "plan": plan,
        "issues": issues,
        "spark_content": spark_content,
        "render": render,
        "docx": docx,
        "out_dir": str(out_dir) if out_dir else None,
    }
    warnings = missing_slots(plan)
    if not approve:
        store.save_checkpoint(run_id, "approve", state)
        store.finish_run(run_id, "interrupted", "", {"node": "approve", "warnings": warnings})
        log(f"  waiting for approval ({run_id})")
        return GraphResult("interrupted", run_id, plan, issues, warnings)
    return _finish(run_id, state, router, log)


def resume_convert(run_id: str, router: Router | None = None, log=print) -> GraphResult:
    state = store.load_checkpoint(run_id)
    if not state:
        raise GraphError(f"no checkpoint for {run_id}")
    if state.get("node") == "done":
        raise GraphError(f"run {run_id} already finished")
    router = router or Router()
    router.run_id = run_id
    store.set_status(run_id, "running")
    log(f"run {run_id}: resume from {state.get('node')}")
    return _finish(run_id, state, router, log)


def _finish(run_id: str, state: dict, router: Router, log) -> GraphResult:
    source = Path(state["source"])
    raw = source.read_text(encoding="utf-8")
    meta, body = split_frontmatter(raw)
    blocks = segment(body)
    out = Path(state["out_dir"]) if state.get("out_dir") else None
    result = execute_plan(
        source,
        router,
        state["plan"],
        list(state.get("issues") or []),
        meta,
        body,
        blocks,
        spark_content=state.get("spark_content"),
        render=bool(state.get("render", True)),
        docx=bool(state.get("docx", False)),
        out_dir=out,
        log=log,
        run_id=run_id,
    )
    verified = run_loop(
        result.markdown,
        router,
        source_body=body,
        pdf=result.pdf,
        archetype=str(state["plan"].get("archetype") or ""),
        log=log,
    )
    markdown, pdf, docx = verified.markdown, result.pdf, result.docx
    if markdown != result.markdown:
        out_dir = out or (ROOT / "output")
        md_path = out_dir / f"{source.stem}.spark.md"
        md_path.write_text(markdown, encoding="utf-8")
        if state.get("render", True):
            from .verify import render_and_check

            pdf, docx, extra = render_and_check(md_path, out_dir, docx=bool(state.get("docx", False)))
            result.issues += extra
    log(f"  verify {verified.status} ({verified.score:.0f})")
    store.save_checkpoint(run_id, "done", state)
    status = "ok" if not result.issues else "ok_with_issues"
    return GraphResult(
        status,
        run_id,
        result.plan,
        result.issues,
        missing_slots(result.plan),
        markdown,
        pdf,
        docx,
        verified.status,
        verified.score,
    )
