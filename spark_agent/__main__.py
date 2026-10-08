"""Docufy CLI. Documents are set in the Spark Technologies template.

    docufy serve                                                             (open Docufy at 127.0.0.1:8787)

    py -m spark_agent convert input/WALL_SEGMENTATION_REPORT.md            (dev profile: local Ollama)
    py -m spark_agent convert file.md --profile claude --docx --spark-content on
    py -m spark_agent convert file.md --no-llm                              (heading-based fallback plan)
    py -m spark_agent run file.md                                            (plan, then wait for approval)
    py -m spark_agent run file.md --approve                                  (plan and finish in one step)
    py -m spark_agent resume RUN_ID                                          (continue a paused run)
    py -m spark_agent verify file.spark.md                                   (check, fix, and stop)
    py -m spark_agent visual file.md --fix                                   (compare components and repair one section)
    py -m spark_agent usage                                                  (token usage per run)
    py -m spark_agent research file.pdf data.csv --question "What changed?"
    py -m spark_agent deep "UAE construction output" --depth quick --secret Constech
    py -m spark_agent serve                                                (open the studio at 127.0.0.1:8787)
    py -m spark_agent ready                                                (first-run check)
    py -m spark_agent backup                                               (snapshot the workspace)
    py -m spark_agent restore workspace.zip
    py -m spark_agent export CHAT_ID -o project.zip
    py -m spark_agent import project.zip
    py -m spark_agent recover                                              (mark crashed runs resumable)
    py -m spark_agent chart chart.yaml -o chart.png
    py -m spark_agent image "a quiet site photograph"                      (off until a provider key is set)
    py -m spark_agent memory                                                 (list remembered preferences and lessons)
    py -m spark_agent governor                                               (show worker caps for this profile)
    py -m spark_agent models                                                 (show context and num_ctx for this profile)
    py -m spark_agent profiles                                               (show dev, claude, and openai)
    py -m spark_agent memory add "no em dashes"
    py -m spark_agent memory confirm ID
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="docufy", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert", help="Smart Convert a markdown file into the Spark template")
    c.add_argument("source")
    c.add_argument("--profile", help="dev | claude | openai (default from config/models.yaml or SPARK_PROFILE)")
    c.add_argument("--spark-content", choices=["auto", "on", "off"], default="auto")
    c.add_argument("--docx", action="store_true")
    c.add_argument("--out", help="output folder (default output/)")
    c.add_argument("--no-llm", action="store_true", help="skip the model and use the heading-based plan")
    g = sub.add_parser("run", help="Plan a document, then wait unless --approve is set")
    g.add_argument("source")
    g.add_argument("--profile")
    g.add_argument("--approve", action="store_true", help="Skip the approval stop and finish the document")
    g.add_argument("--no-llm", action="store_true")
    g.add_argument("--docx", action="store_true")
    g.add_argument("--out")
    r = sub.add_parser("resume", help="Finish a run that is waiting for approval")
    r.add_argument("run_id")
    r.add_argument("--profile")
    r.add_argument("--no-llm", action="store_true")
    v = sub.add_parser("verify", help="Run the verify loop on a Spark markdown file")
    v.add_argument("source")
    v.add_argument("--profile")
    v.add_argument("--no-llm", action="store_true")
    v.add_argument("--archetype", default="")
    vis = sub.add_parser("visual", help="Compare components with the theme exemplars and optionally repair them")
    vis.add_argument("source")
    vis.add_argument("--fix", action="store_true")
    vis.add_argument("--no-llm", action="store_true")
    vis.add_argument("--profile")
    sub.add_parser("usage", help="show recent runs and token usage")
    gov = sub.add_parser("governor", help="Show the worker caps for a profile")
    gov.add_argument("--profile")
    models = sub.add_parser("models", help="Show the context window and num_ctx for a profile")
    models.add_argument("--profile")
    sub.add_parser("profiles", help="Show the dev, claude, and openai profiles")
    chart = sub.add_parser("chart", help="Draw a flat Spark chart from a YAML spec")
    chart.add_argument("spec")
    chart.add_argument("-o", "--out", required=True)
    picture = sub.add_parser("image", help="Generate a figure. Logos and diagrams are refused.")
    picture.add_argument("prompt")
    picture.add_argument("--provider", default="openai", choices=["openai", "gemini", "stability", "flux", "comfy"])
    picture.add_argument("--seed", type=int, default=1)
    picture.add_argument("-o", "--out")
    serve = sub.add_parser("serve", help="Open Docufy in the browser")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--no-open", action="store_true", help="Do not open a browser window")
    sub.add_parser("mcp", help="Serve the Spark template to other agents over MCP (stdio)")
    sub.add_parser("ready", help="Check Python, the database, fonts, and the local model")
    sub.add_parser("recover", help="Mark crashed runs so they can resume")
    backup = sub.add_parser("backup", help="Snapshot the database and studio files")
    backup.add_argument("-o", "--out")
    restore = sub.add_parser("restore", help="Replace the workspace from a backup zip")
    restore.add_argument("archive")
    export = sub.add_parser("export", help="Export one chat as a zip")
    export.add_argument("chat_id")
    export.add_argument("-o", "--out", required=True)
    imported = sub.add_parser("import", help="Import a chat zip")
    imported.add_argument("archive")
    deep = sub.add_parser("deep", help="Research a question on the web and write a brief for approval")
    deep.add_argument("question")
    deep.add_argument("--depth", choices=["quick", "standard", "deep"], default="quick")
    deep.add_argument("--secret", action="append", default=[], help="client name or figure to keep out of search queries")
    deep.add_argument("--approve", action="store_true")
    deep.add_argument("--out")
    res = sub.add_parser("research", help="Read uploaded files and write a brief for approval")
    res.add_argument("paths", nargs="+")
    res.add_argument("--question", default="")
    res.add_argument("--approve", action="store_true", help="Mark the brief approved")
    res.add_argument("--out", help="brief markdown path")
    mem = sub.add_parser("memory", help="list, add, confirm, disable, or delete remembered lessons")
    mem.add_argument("action", nargs="?", default="list", choices=["list", "add", "fact", "confirm", "disable", "delete"])
    mem.add_argument("text", nargs="?", default="")
    mem.add_argument("--scope", default="user", choices=["project", "client", "user", "global"])
    mem.add_argument("--key", default="", help="client or project name when the scope needs one")
    mem.add_argument("--fix", default="")
    args = ap.parse_args(argv)

    if args.cmd == "chart":
        import yaml

        from spark_docs.charts import render_chart

        spec = yaml.safe_load(Path(args.spec).read_text(encoding="utf-8")) or {}
        path = render_chart(spec, Path(args.out))
        print(f"chart     {path}")
        return 0

    if args.cmd == "image":
        from spark_agent.imagegen import ImageError, generate_image

        try:
            asset = generate_image(args.prompt, provider=args.provider, seed=args.seed)
        except ImageError as exc:
            print(exc)
            return 1
        if args.out:
            Path(args.out).write_bytes(Path(asset.path).read_bytes())
            print(f"image     {args.out}")
        else:
            print(f"image     {asset.path}")
        print(f"provider  {asset.provider}")
        print(f"cached    {asset.cached}")
        return 0

    if args.cmd == "mcp":
        from spark_agent.mcp_server import main as serve_mcp

        serve_mcp()
        return 0

    if args.cmd == "serve":
        import threading
        import webbrowser

        import uvicorn

        from spark_agent.ops import BIND_HOST, startup

        startup(backup=True)
        url = f"http://{BIND_HOST}:{args.port}"
        print(f"docufy    {url}")
        print("web       npm run dev --prefix web")
        if not args.no_open:
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        uvicorn.run("spark_agent.server:app", host=BIND_HOST, port=args.port)
        return 0

    if args.cmd == "ready":
        from spark_agent.ops import ready_checks, ready_ok

        checks = ready_checks()
        for item in checks:
            state = "ok" if item["ok"] else "fix"
            print(f"{state:<6}{item['name']:<12}{item['detail']}")
            if item["fix"] and not item["ok"]:
                print(f"       {item['fix']}")
        return 0 if ready_ok(checks) else 1

    if args.cmd == "recover":
        from spark_agent.ops import recover_runs

        found = recover_runs()
        if not found:
            print("nothing to recover")
            return 0
        for run_id in found:
            print(f"resumable {run_id}")
        return 0

    if args.cmd == "backup":
        from spark_agent.ops import backup_workspace

        path = backup_workspace(Path(args.out) if args.out else None)
        print(f"backup    {path}")
        return 0

    if args.cmd == "restore":
        from spark_agent.ops import OpsError, restore_workspace

        try:
            restore_workspace(Path(args.archive))
        except OpsError as exc:
            print(exc)
            return 1
        print(f"restored  {args.archive}")
        return 0

    if args.cmd == "export":
        from spark_agent.ops import export_chat
        from spark_agent.studio import StudioError

        try:
            path = export_chat(args.chat_id, Path(args.out))
        except StudioError as exc:
            print(exc)
            return 1
        print(f"export    {path}")
        return 0

    if args.cmd == "import":
        from spark_agent.ops import OpsError, import_project

        try:
            chat = import_project(Path(args.archive))
        except OpsError as exc:
            print(exc)
            return 1
        print(f"import    {chat['id']}  {chat['title']}")
        return 0

    if args.cmd == "run":
        from spark_agent.graph import start_convert
        from spark_agent.llm import Router

        router = Router(profile=args.profile or "")
        if args.no_llm:
            router.fake = _no_llm
        res = start_convert(
            Path(args.source),
            router,
            approve=args.approve,
            docx=args.docx,
            out_dir=Path(args.out) if args.out else None,
        )
        _print_graph(res)
        return 0

    if args.cmd == "resume":
        from spark_agent.graph import resume_convert
        from spark_agent.llm import Router

        router = Router(profile=args.profile or "")
        if args.no_llm:
            router.fake = _no_llm
        res = resume_convert(args.run_id, router)
        _print_graph(res)
        return 0

    if args.cmd == "verify":
        from spark_agent.llm import Router
        from spark_agent.loop import run_loop

        router = Router(profile=args.profile or "")
        if args.no_llm:
            router.fake = _no_llm
        src = Path(args.source)
        res = run_loop(src.read_text(encoding="utf-8"), router, archetype=args.archetype)
        if res.markdown != src.read_text(encoding="utf-8"):
            src.write_text(res.markdown, encoding="utf-8")
            print(f"updated   {src}")
        print(f"verify    {res.status} ({res.score:.0f})")
        print(res.reason)
        for item in res.issues:
            print(f"  - {item.severity} {item.code}: {item.message}")
        return 0 if res.status == "clean" else 1

    if args.cmd == "visual":
        from spark_agent.llm import Router
        from spark_agent.visual import review
        from spark_docs.ir import load_markdown, to_spark_markdown

        src = Path(args.source)
        doc = load_markdown(src.read_text(encoding="utf-8"))
        router = None if args.no_llm else Router(profile=args.profile or "")
        report = review(doc, router, apply=args.fix)
        if args.fix:
            src.write_text(to_spark_markdown(report.doc), encoding="utf-8")
        for item in report.findings:
            print(f"{item.action or 'mismatch'} {item.block_id} {item.kind} -> {item.exemplar}")
            for signal in item.signals:
                if signal.different:
                    print(f"  - {signal.name}: {signal.detail}")
        if report.escalated:
            print("escalated: " + ", ".join(report.escalated))
        return 0 if not report.findings or args.fix and not report.escalated else 1

    if args.cmd == "deep":
        from spark_agent.deep import render_deep, run_research

        run = run_research(args.question, depth=args.depth, secrets=args.secret, approved=args.approve)
        dest = Path(args.out) if args.out else ROOT / "output" / "deep_brief.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(render_deep(run), encoding="utf-8")
        print(render_deep(run))
        print(f"brief     {dest}")
        return 0

    if args.cmd == "research":
        from spark_agent.research import render_brief, research_files, write_brief

        brief, docs = research_files(args.paths, args.question, approved=args.approve)
        dest = Path(args.out) if args.out else ROOT / "output" / "research_brief.md"
        write_brief(brief, dest)
        print(render_brief(brief))
        print(f"brief     {dest}")
        print(f"sources   {len(docs)}")
        return 0

    if args.cmd == "memory":
        from spark_agent import memory

        if args.action == "add":
            print(memory.remember("preference", args.text, scope=args.scope, scope_key=args.key, fix=args.fix))
            return 0
        if args.action == "fact":
            print(memory.remember("fact", args.fix or args.text, scope="client", scope_key=args.key or args.text))
            return 0
        if args.action == "confirm":
            memory.confirm(args.text)
            print(f"active {args.text}")
            return 0
        if args.action == "disable":
            memory.disable(args.text)
            print(f"disabled {args.text}")
            return 0
        if args.action == "delete":
            memory.delete(args.text)
            print(f"deleted {args.text}")
            return 0
        rows = memory.list_memories()
        if not rows:
            print("nothing remembered")
            return 0
        for row in rows:
            print(f"{row['id']}  {row['status']:<10} {row['scope']:<8} {row['kind']:<12} {row['mistake']}")
        return 0

    if args.cmd == "profiles":
        from spark_agent.llm import profile_report

        for line in profile_report():
            print(line)
        return 0

    if args.cmd == "models":
        from spark_agent.llm import Router
        from spark_agent.tokens import describe_model

        router = Router(profile=args.profile or "")
        configured = int((router.config.get("ollama") or {}).get("num_ctx", 16384))
        table = router.config.get("models") or {}
        roles = (router.config.get("profiles") or {}).get(router.profile) or {}
        print(f"profile {router.profile}")
        for role, name in roles.items():
            print(f"role {role}")
            for line in describe_model(str(name), table, configured=configured):
                print(f"  {line}")
        return 0

    if args.cmd == "governor":
        from spark_agent.governor import describe
        from spark_agent.llm import Router

        for line in describe(Router(profile=args.profile or "")):
            print(line)
        return 0

    if args.cmd == "usage":
        from spark_agent import store

        rows = store.conn().execute(
            "SELECT r.id, r.kind, r.profile, r.status, datetime(r.started_at,'unixepoch','localtime'), "
            "COALESCE(SUM(c.input_tokens),0), COALESCE(SUM(c.output_tokens),0), COUNT(c.id), r.source "
            "FROM runs r LEFT JOIN llm_calls c ON c.run_id = r.id GROUP BY r.id ORDER BY r.started_at DESC LIMIT 20"
        ).fetchall()
        print(f"{'run':<13}{'profile':<8}{'status':<16}{'started':<21}{'in tok':>8}{'out tok':>8}{'calls':>6}  source")
        for r in rows:
            print(f"{r[0]:<13}{r[2]:<8}{r[3]:<16}{r[4]:<21}{r[5]:>8}{r[6]:>8}{r[7]:>6}  {Path(r[8]).name}")
        return 0

    from spark_agent.convert import convert, fallback_plan  # noqa: F401
    from spark_agent.llm import Router

    router = Router(profile=args.profile or "")
    if args.no_llm:
        router.fake = _no_llm
    spark = {"auto": None, "on": True, "off": False}[args.spark_content]
    res = convert(Path(args.source), router, spark_content=spark, docx=args.docx, out_dir=Path(args.out) if args.out else None)
    print(f"markdown  {Path(args.out or ROOT / 'output') / (Path(args.source).stem + '.spark.md')}")
    if res.pdf:
        print(f"PDF       {res.pdf}")
    if res.docx:
        print(f"DOCX      {res.docx}")
    if res.issues:
        print("issues:")
        for i in res.issues:
            print(f"  - {i}")
    else:
        print("checks passed")
    return 0


def _print_graph(res) -> None:
    print(f"run {res.run_id}  {res.status}")
    if res.warnings:
        print("slot warnings:")
        for item in res.warnings:
            print(f"  - {item}")
    if res.verify_status:
        print(f"verify    {res.verify_status} ({res.verify_score:.0f})")
    if res.pdf:
        print(f"PDF       {res.pdf}")
    if res.docx:
        print(f"DOCX      {res.docx}")
    if res.issues:
        print("issues:")
        for item in res.issues:
            print(f"  - {item}")
    elif res.status != "interrupted":
        print("checks passed")


def _no_llm(model, messages, schema):
    raise_from = __import__("spark_agent.llm", fromlist=["LLMError"]).LLMError
    raise raise_from("model disabled (--no-llm)")


if __name__ == "__main__":
    raise SystemExit(main())
