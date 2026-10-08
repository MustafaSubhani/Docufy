import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type ChatDetail, type DocumentInfo, type DraftDetail, type DraftInfo } from "../api";
import { ArrowUp, Check, ChevronDown, Close, Copy, Download, FileText, Pages, Pen, Spinner } from "../icons";
import { useDismiss, when } from "../lib";
import { Markdown } from "./Markdown";
import { Preview } from "./Preview";

export type PanelTab = "draft" | "pages";

type Props = {
  chat: ChatDetail;
  tab: PanelTab;
  onTab: (tab: PanelTab) => void;
  draftRoot: string;
  onDraftRoot: (root: string) => void;
  doc: DocumentInfo | null;
  onPickDoc: (doc: DocumentInfo) => void;
  busy: boolean;
  spark: boolean;
  onSend: (text: string, sectionIds?: string[], targetDoc?: string | null) => Promise<boolean>;
  onPublish: (draftId: string, clientPages: boolean) => Promise<void>;
  onChat: (detail: ChatDetail) => void;
  onResize: (width: number) => void;
  onClose: () => void;
};

export function Workspace(props: Props) {
  const drafts = props.chat.drafts;
  const draft = drafts.find((item) => item.root_id === props.draftRoot) ?? drafts.at(-1) ?? null;
  const tab: PanelTab = props.tab === "draft" && draft ? "draft" : props.doc ? "pages" : draft ? "draft" : "pages";
  const writingDoc = props.busy && props.doc ? props.chat.messages.some((item) => item.status === "writing" && item.role === "assistant") : false;

  return (
    <aside className="preview workspace" aria-label="Workspace">
      <div
        className="resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the workspace"
        onPointerDown={(event) => {
          event.preventDefault();
          const handle = event.currentTarget;
          handle.setPointerCapture(event.pointerId);
          document.body.classList.add("resizing");
          const move = (next: PointerEvent) => props.onResize(window.innerWidth - next.clientX);
          const up = () => {
            document.body.classList.remove("resizing");
            handle.removeEventListener("pointermove", move);
            handle.removeEventListener("pointerup", up);
          };
          handle.addEventListener("pointermove", move);
          handle.addEventListener("pointerup", up);
        }}
        onDoubleClick={() => props.onResize(0)}
      />
      <div className="ws-tabs" role="tablist" aria-label="Workspace view">
        <button type="button" role="tab" aria-selected={tab === "draft"} className={tab === "draft" ? "on" : undefined} disabled={!draft} onClick={() => props.onTab("draft")}>
          <FileText size={15} />
          Draft
          {draft ? <span className="ws-tab-meta">v{draft.version}</span> : null}
        </button>
        <button type="button" role="tab" aria-selected={tab === "pages"} className={tab === "pages" ? "on" : undefined} disabled={!props.doc} onClick={() => props.onTab("pages")}>
          <Pages size={15} />
          Pages
          {props.doc ? <span className="ws-tab-meta">v{props.doc.version || 1}</span> : null}
        </button>
        <span className="spacer" />
        <button type="button" className="icon-button" aria-label="Close the workspace" onClick={props.onClose}>
          <Close />
        </button>
      </div>
      {tab === "draft" && draft ? (
        <DraftView
          key={draft.root_id}
          info={draft}
          drafts={drafts}
          onPick={props.onDraftRoot}
          busy={props.busy}
          spark={props.spark}
          onSend={(text) => props.onSend(text, undefined, null)}
          onPublish={props.onPublish}
          onOpenPages={() => props.onTab("pages")}
          published={Boolean(draft.document_root && props.chat.documents.some((item) => (item.root_id || item.id) === draft.document_root))}
        />
      ) : props.doc ? (
        <Preview
          doc={props.doc}
          chat={props.chat}
          busy={writingDoc}
          onPick={props.onPickDoc}
          onApplyEdit={(text, sections) => props.onSend(text, sections, props.doc?.id ?? null)}
          onChat={props.onChat}
        />
      ) : (
        <div className="ws-empty">
          <p>Nothing to show yet.</p>
        </div>
      )}
    </aside>
  );
}

const DRAFT_ASKS = [
  ["Tighten", "Tighten the draft: cut repetition and filler, keep every fact."],
  ["Add a diagram", "Add a mermaid diagram where a flow or architecture would help the reader most."],
  ["Add a timeline", "Add an implementation timeline table with phases, deliverables and durations. Mark unknown durations to be confirmed."],
  ["Check facts", "Check the draft's factual claims against the web and fix or mark anything you cannot confirm."],
] as const;

function DraftView({
  info,
  drafts,
  onPick,
  busy,
  spark,
  onSend,
  onPublish,
  onOpenPages,
  published,
}: {
  info: DraftInfo;
  drafts: DraftInfo[];
  onPick: (root: string) => void;
  busy: boolean;
  spark: boolean;
  onSend: (text: string) => Promise<boolean>;
  onPublish: (draftId: string, clientPages: boolean) => Promise<void>;
  onOpenPages: () => void;
  published: boolean;
}) {
  const [detail, setDetail] = useState<DraftDetail | null>(null);
  const [viewId, setViewId] = useState(info.id);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [ask, setAsk] = useState("");
  const [quote, setQuote] = useState<{ text: string; x: number; y: number } | null>(null);
  const [copied, setCopied] = useState(false);
  const [flash, setFlash] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  const askRef = useRef<HTMLTextAreaElement>(null);
  const latestId = useRef(info.id);

  const load = useCallback((id: string) => {
    api
      .draft(id)
      .then((data) => {
        setDetail(data);
        setViewId(data.id);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (latestId.current !== info.id) {
      const following = !detail || detail.id === latestId.current;
      latestId.current = info.id;
      if (following && !editing) {
        load(info.id);
        setFlash(true);
        window.setTimeout(() => setFlash(false), 1400);
      }
      return;
    }
    if (!detail) load(info.id);
  }, [info.id, detail, editing, load]);

  const outline = useMemo(() => headings(detail?.body ?? ""), [detail?.body]);
  const old = detail && detail.id !== detail.latest_id && detail.id !== info.id;

  function jump(index: number) {
    const nodes = body.current?.querySelectorAll("h1, h2, h3");
    nodes?.[index]?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function onSelect() {
    const selection = window.getSelection();
    const value = selection?.toString().trim() ?? "";
    if (!value || value.length < 3 || !selection?.rangeCount || !body.current) {
      setQuote(null);
      return;
    }
    const rect = selection.getRangeAt(0).getBoundingClientRect();
    const box = body.current.getBoundingClientRect();
    setQuote({ text: value.slice(0, 400), x: rect.left - box.left + rect.width / 2, y: rect.top - box.top + body.current.scrollTop - 8 });
  }

  async function send(value: string) {
    const trimmed = value.trim();
    if (!trimmed || busy) return;
    const ok = await onSend(`${trimmed}${trimmed.includes(info.title) ? "" : ` (in the draft "${info.title}")`}`);
    if (ok) setAsk("");
  }

  async function save() {
    if (!detail) return;
    setSaving(true);
    try {
      const saved = await api.saveDraft(detail.id, text);
      setDetail(saved);
      latestId.current = saved.id;
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="draft">
      <header className="draft-head">
        <div className="draft-title">
          <h2>{info.title}</h2>
          <p>
            <span>{(detail?.words ?? info.words).toLocaleString()} words</span>
            <VersionPick detail={detail} onView={load} />
            {drafts.length > 1 ? (
              <select className="draft-pick" value={info.root_id} onChange={(event) => onPick(event.target.value)} aria-label="Choose a draft">
                {drafts.map((item) => (
                  <option key={item.root_id} value={item.root_id}>
                    {item.title}
                  </option>
                ))}
              </select>
            ) : null}
            {busy ? (
              <span className="live-badge">
                <Spinner size={11} />
                Docufy is working
              </span>
            ) : null}
          </p>
        </div>
        <div className="draft-actions">
          {editing ? (
            <>
              <button type="button" className="small-button" onClick={() => setEditing(false)}>
                Cancel
              </button>
              <button type="button" className="small-button primary" disabled={saving || text === detail?.body} onClick={save}>
                {saving ? "Saving" : "Save as new version"}
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="icon-button"
                title="Edit the Markdown"
                aria-label="Edit the Markdown"
                disabled={!detail}
                onClick={() => {
                  setText(detail?.body ?? "");
                  setEditing(true);
                }}
              >
                <Pen size={17} />
              </button>
              <button
                type="button"
                className="icon-button"
                title={copied ? "Copied" : "Copy the Markdown"}
                aria-label="Copy the Markdown"
                onClick={() =>
                  detail &&
                  navigator.clipboard?.writeText(detail.body).then(() => {
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 1200);
                  })
                }
              >
                {copied ? <Check size={17} /> : <Copy size={17} />}
              </button>
              <button
                type="button"
                className="icon-button"
                title="Download Markdown"
                aria-label="Download Markdown"
                disabled={!detail}
                onClick={() => detail && download(`${info.title}.md`, detail.body)}
              >
                <Download size={17} />
              </button>
              <PublishButton
                busy={busy}
                spark={spark}
                published={published}
                onPublish={(client) => onPublish(info.id, client)}
                onOpenPages={onOpenPages}
              />
            </>
          )}
        </div>
      </header>
      {old ? (
        <div className="draft-old">
          Viewing version {detail?.version}.
          <button type="button" className="link-button" onClick={() => load(info.id)}>
            Back to the latest
          </button>
        </div>
      ) : null}
      {editing ? (
        <textarea className="draft-editor" value={text} spellCheck onChange={(event) => setText(event.target.value)} aria-label="Draft Markdown" />
      ) : (
        <div className="draft-scroll" ref={body} onMouseUp={onSelect} onScroll={() => setQuote(null)}>
          {outline.length > 2 ? (
            <nav className="draft-outline" aria-label="Outline">
              {outline.map((item, index) => (
                <button key={index} type="button" className={`lvl-${item.level}`} onClick={() => jump(index)}>
                  {item.text}
                </button>
              ))}
            </nav>
          ) : null}
          <article className={flash ? "draft-paper flash" : "draft-paper"} key={viewId}>
            {detail ? <Markdown text={detail.body} variant="doc" /> : <div className="draft-loading" />}
          </article>
          {quote ? (
            <button
              type="button"
              className="quote-ask"
              style={{ left: quote.x, top: quote.y }}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                setAsk(`About "${quote.text.length > 160 ? `${quote.text.slice(0, 160)}...` : quote.text}": `);
                setQuote(null);
                window.setTimeout(() => {
                  askRef.current?.focus();
                  const length = askRef.current?.value.length ?? 0;
                  askRef.current?.setSelectionRange(length, length);
                }, 0);
              }}
            >
              <Pen size={13} />
              Ask about this
            </button>
          ) : null}
        </div>
      )}
      {!editing ? (
        <div className="ask-dock">
          <div className="ask-chips">
            {DRAFT_ASKS.map(([label, prompt]) => (
              <button key={label} type="button" className="ask-chip" disabled={busy} onClick={() => send(prompt)}>
                {label}
              </button>
            ))}
          </div>
          <form
            className="ask-form"
            onSubmit={(event) => {
              event.preventDefault();
              send(ask);
            }}
          >
            <textarea
              ref={askRef}
              rows={1}
              value={ask}
              placeholder="Ask Docufy to change the draft"
              aria-label="Change the draft"
              onChange={(event) => setAsk(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  send(ask);
                }
              }}
            />
            <button type="submit" className="send small" disabled={busy || !ask.trim()} aria-label="Send">
              <ArrowUp size={16} />
            </button>
          </form>
        </div>
      ) : null}
    </div>
  );
}

function VersionPick({ detail, onView }: { detail: DraftDetail | null; onView: (id: string) => void }) {
  if (!detail || detail.versions.length < 2) return detail ? <span>Version {detail.version}</span> : null;
  return (
    <label className="version-select">
      <span className="visually-hidden">Version</span>
      <select value={detail.id} onChange={(event) => onView(event.target.value)}>
        {[...detail.versions].reverse().map((item) => (
          <option key={item.id} value={item.id}>
            v{item.version} · {item.author === "user" ? "your edit" : item.note || "Docufy"} · {when(item.created_at)}
          </option>
        ))}
      </select>
    </label>
  );
}

function PublishButton({
  busy,
  spark,
  published,
  onPublish,
  onOpenPages,
}: {
  busy: boolean;
  spark: boolean;
  published: boolean;
  onPublish: (clientPages: boolean) => Promise<void>;
  onOpenPages: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [client, setClient] = useState(spark);
  const [sending, setSending] = useState(false);
  const ref = useDismiss<HTMLDivElement>(open, () => setOpen(false));
  useEffect(() => setClient(spark), [spark]);
  return (
    <div className="download-menu" ref={ref}>
      <button type="button" className="small-button primary publish" disabled={busy || sending} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        {busy || sending ? <Spinner size={13} /> : <Pages size={15} />}
        {published ? "Update the pages" : "Set in template"}
        <ChevronDown size={14} />
      </button>
      {open ? (
        <div className="popover right publish-pop" role="dialog" aria-label="Set in the template">
          <p className="popover-title">Spark Technologies template</p>
          <label className="publish-row">
            <input type="radio" name="pages" checked={client} onChange={() => setClient(true)} />
            <span>
              <strong>Client document</strong>
              <em>Adds Who We Are, Why Choose Us and the contact pages</em>
            </span>
          </label>
          <label className="publish-row">
            <input type="radio" name="pages" checked={!client} onChange={() => setClient(false)} />
            <span>
              <strong>Internal document</strong>
              <em>Your content only, no company pages</em>
            </span>
          </label>
          <button
            type="button"
            className="primary-button wide"
            onClick={() => {
              setSending(true);
              onPublish(client)
                .then(() => setOpen(false))
                .finally(() => setSending(false));
            }}
          >
            {published ? "Make the next version" : "Set the pages"}
          </button>
          {published ? (
            <button type="button" className="popover-link" onClick={onOpenPages}>
              Show the current pages
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function headings(markdown: string) {
  const out: { level: number; text: string }[] = [];
  let fence = false;
  for (const line of markdown.split("\n")) {
    if (line.trimStart().startsWith("```")) fence = !fence;
    if (fence) continue;
    const match = /^(#{1,3})\s+(.+?)\s*#*\s*$/.exec(line);
    if (match) out.push({ level: match[1].length, text: match[2].replace(/[*_`]/g, "") });
  }
  return out;
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name.replace(/[^\w .,()&-]+/g, " ").trim() || "draft.md";
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}
