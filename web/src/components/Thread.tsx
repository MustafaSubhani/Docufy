import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { api, type ChatDetail, type DocumentInfo, type DraftInfo, type Message, type ModelChoice, type Models, type Source } from "../api";
import { ArrowDown, Check, Copy, Download, FileText, Layout, List, Mark, Pages, Pen, Refresh, Search, Upload } from "../icons";
import { pages, Rich, when } from "../lib";
import { ChatActions } from "../openui/chat";
import { isResearchBody } from "../openui/extract";
import { Activity } from "./Activity";
import { Composer, type ComposerHandle } from "./Composer";
import { Markdown } from "./Markdown";
import { ResearchReply } from "./ResearchReply";

const STARTERS = [
  { icon: Pen, title: "Write a proposal", note: "Research the client, draft every section, then set it in the template", text: "Write a client proposal for ", spark: true },
  { icon: Search, title: "Research a market", note: "Live web search with sources you can click", text: "Research the current state of ", spark: true },
  { icon: List, title: "Plan a project", note: "A plan with phases, a timeline table, and a flow diagram", text: "Write a plan of action for ", spark: false },
  { icon: Layout, title: "Set my notes as pages", note: "Attach or paste notes and keep every word", text: "Set these notes in the Spark template:\n\n", spark: true },
];

type Props = {
  chat: ChatDetail | null;
  loading: boolean;
  preview: DocumentInfo | null;
  spark: boolean;
  onSpark: (value: boolean) => void;
  web: boolean;
  onWeb: (value: boolean) => void;
  model: ModelChoice;
  models: Models | null;
  onModel: (value: ModelChoice) => void;
  editing: boolean;
  onEditing: (value: boolean) => void;
  onSend: (text: string, file: File | null) => Promise<boolean>;
  onStop: (messageId: string) => void;
  onRetry: (messageId: string, model?: ModelChoice) => void;
  onOpen: (doc: DocumentInfo) => void;
  onOpenDraft: (draft: DraftInfo) => void;
  openDraftId: string;
  composerRef: RefObject<ComposerHandle | null>;
};

export function Thread(props: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const [away, setAway] = useState(false);
  const [dragging, setDragging] = useState(false);
  const messages = props.chat?.messages ?? [];
  const writing = messages.find((item) => item.status === "writing");
  const last = messages.at(-1);
  const signature = `${messages.length}:${last?.status}:${last?.body.length}:${last?.activity?.length ?? 0}`;
  const actions = { busy: Boolean(writing), send: (text: string) => void props.onSend(text, null) };

  useLayoutEffect(() => {
    pinned.current = true;
    setAway(false);
    const node = scroller.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [props.chat?.id]);

  useEffect(() => {
    const node = scroller.current;
    if (!node) return;
    if (pinned.current) {
      node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
    } else {
      setAway(true);
    }
  }, [signature]);

  function onScroll() {
    const node = scroller.current;
    if (!node) return;
    const near = node.scrollHeight - node.scrollTop - node.clientHeight < 140;
    pinned.current = near;
    if (near) setAway(false);
  }

  function toBottom() {
    const node = scroller.current;
    pinned.current = true;
    setAway(false);
    node?.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
  }

  const empty = !props.loading && messages.length === 0;
  const composer = (
    <Composer
      ref={props.composerRef}
      busy={Boolean(writing)}
      spark={props.spark}
      onSpark={props.onSpark}
      web={props.web}
      onWeb={props.onWeb}
      model={props.model}
      models={props.models}
      onModel={props.onModel}
      target={props.preview}
      editing={props.editing}
      onEditing={props.onEditing}
      onSend={async (text, file) => {
        pinned.current = true;
        return props.onSend(text, file);
      }}
      onStop={() => writing && props.onStop(writing.id)}
    />
  );

  return (
    <div
      className="thread"
      onDragOver={(event) => {
        if (event.dataTransfer.types.includes("Files")) {
          event.preventDefault();
          setDragging(true);
        }
      }}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        const dropped = event.dataTransfer.files?.[0];
        if (dropped) props.composerRef.current?.attach(dropped);
      }}
    >
      {dragging ? (
        <div className="dropzone" onDragLeave={() => setDragging(false)}>
          <Upload size={26} />
          <p>Drop the file to attach it</p>
          <span>Word, PDF, slides, spreadsheets, CSV, Markdown, or text</span>
        </div>
      ) : null}
      {empty ? (
        <div className="welcome">
          <div className="welcome-inner">
            <div className="welcome-mark">
              <Mark size={40} />
            </div>
            <h1>What are we writing today?</h1>
            <p>Ask for research, a proposal, or a plan. Docufy searches the web, drafts it in the canvas with tables and diagrams, then sets it in the Spark Technologies template when you say so.</p>
            {composer}
            <div className="starters">
              {STARTERS.map((item, index) => (
                <button
                  key={item.title}
                  type="button"
                  className="starter"
                  style={{ animationDelay: `${120 + index * 50}ms` }}
                  onClick={() => {
                    props.onSpark(item.spark);
                    props.composerRef.current?.fill(item.text);
                  }}
                >
                  <item.icon size={18} />
                  <span className="starter-text">
                    <span className="starter-title">{item.title}</span>
                    <span className="starter-note">{item.note}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <>
          <div className="messages" ref={scroller} onScroll={onScroll}>
            <ChatActions.Provider value={actions}>
              {props.loading ? (
                <Skeleton />
              ) : (
                messages.map((message, index) => (
                  <MessageView
                    key={message.id}
                    message={message}
                    chat={props.chat!}
                    models={props.models}
                    openId={props.preview?.id ?? ""}
                    openDraftId={props.openDraftId}
                    latest={index === messages.length - 1}
                    onOpen={props.onOpen}
                    onOpenDraft={props.onOpenDraft}
                    onStop={() => props.onStop(message.id)}
                    onRetry={(model) => props.onRetry(message.id, model)}
                  />
                ))
              )}
            </ChatActions.Provider>
          </div>
          <div className="composer-dock">
            {away ? (
              <button type="button" className="jump" onClick={toBottom}>
                <ArrowDown size={15} />
                {writing ? "Follow the work" : "Latest"}
              </button>
            ) : null}
            {composer}
            <p className="composer-hint">
              <kbd>Enter</kbd> sends, <kbd>Shift</kbd> <kbd>Enter</kbd> adds a line, <kbd>Ctrl</kbd> <kbd>K</kbd> finds anything
            </p>
          </div>
        </>
      )}
    </div>
  );
}

function MessageView({
  message,
  chat,
  models,
  openId,
  openDraftId,
  latest,
  onOpen,
  onOpenDraft,
  onStop,
  onRetry,
}: {
  message: Message;
  chat: ChatDetail;
  models: Models | null;
  openId: string;
  openDraftId: string;
  latest: boolean;
  onOpen: (doc: DocumentInfo) => void;
  onOpenDraft: (draft: DraftInfo) => void;
  onStop: () => void;
  onRetry: (model?: ModelChoice) => void;
}) {
  if (message.role === "user") {
    const target = chat.documents.find((item) => item.id === message.target_id);
    const scoped = message.sections ?? [];
    return (
      <article className="bubble user">
        {message.attachment ? (
          <span className="attachment">
            <FileText size={15} />
            {message.attachment}
          </span>
        ) : null}
        {target || scoped.length ? (
          <span className="target">
            <Pen size={13} />
            {target ? `${target.title}${target.version && target.version > 1 ? ` v${target.version}` : ""}` : "Open document"}
            {scoped.length ? ` · ${scoped.length} section${scoped.length === 1 ? "" : "s"}` : ""}
          </span>
        ) : null}
        {message.web ? <span className="target">Web</span> : null}
        {message.body && message.body !== message.attachment ? <UserText text={message.body} /> : null}
      </article>
    );
  }

  const doc = chat.documents.find((item) => item.id === message.document_id);
  const live = message.status === "writing";
  const streaming = live && Boolean(message.progress?.streaming);
  const steps = message.activity ?? [];
  const usable = (models?.options ?? []).filter((item) => item.id !== "off" && item.available);
  const agentic = steps.some((step) => step.kind === "tool" || step.kind === "thought") || Boolean(message.usage);
  const drafts = (message.drafts ?? [])
    .map((id) => chat.drafts.find((item) => item.root_id === id || item.id === id) ?? null)
    .filter((item): item is DraftInfo => Boolean(item));
  const draftRoots = Array.from(new Set(drafts.map((item) => item.root_id)));
  const latestDrafts = draftRoots
    .map((root) => chat.drafts.find((item) => item.root_id === root))
    .filter((item): item is DraftInfo => Boolean(item));
  const showReply = !live || streaming;

  return (
    <article className={live ? "bubble assistant live" : "bubble assistant"}>
      <header className="who">
        <Mark size={22} />
        <span>Docufy</span>
        {message.model_label ? <span className="who-model">{message.usage?.model || message.model_label}</span> : null}
        {!live && message.body ? <CopyButton text={message.body} /> : null}
      </header>
      {live || steps.length ? (
        <Activity
          steps={steps}
          live={live}
          fallback={live ? message.progress?.text || (streaming ? "" : message.body) : ""}
          outcome={message.status}
          onStop={onStop}
          compact={!live && !latest}
        />
      ) : null}
      {message.status === "blocked" ? (
        <div className="needs">
          <p>{message.body}</p>
          <div className="needs-actions">
            {usable.length ? (
              usable.map((item) => (
                <button key={item.id} type="button" className="small-button primary" onClick={() => onRetry(item.id)}>
                  <Pen size={14} />
                  Write it with {item.label}
                </button>
              ))
            ) : (
              <span className="hint">Start Ollama or add a key in Settings, then try again.</span>
            )}
          </div>
        </div>
      ) : showReply ? (
        <div className={message.status === "failed" ? "reply failed" : message.status === "stopped" ? "reply muted" : "reply"}>
          {!agentic && looksLikeResearch(message) ? (
            <ResearchReply text={message.body} streaming={streaming} />
          ) : agentic || /```|\n\|/.test(message.body) ? (
            <Markdown text={message.body} streaming={streaming} />
          ) : (
            <Rich text={message.body} />
          )}
          {streaming ? <span className="caret" /> : null}
        </div>
      ) : null}
      {!live && message.sources?.length ? <SourceStrip sources={message.sources} /> : null}
      {latestDrafts.length ? (
        <div className="draft-cards">
          {latestDrafts.map((draft) => (
            <DraftCard key={draft.root_id} draft={draft} open={openDraftId === draft.root_id} onOpen={() => onOpenDraft(draft)} />
          ))}
        </div>
      ) : null}
      {message.status === "failed" || message.status === "stopped" ? (
        <button type="button" className="ghost-button retry" onClick={() => onRetry()}>
          <Refresh size={15} />
          Try again
        </button>
      ) : null}
      {doc ? <DocCard doc={doc} open={doc.id === openId} onOpen={() => onOpen(doc)} /> : null}
      {!live && message.usage && latest ? <UsageLine usage={message.usage} /> : null}
    </article>
  );
}

function SourceStrip({ sources }: { sources: Source[] }) {
  const [all, setAll] = useState(false);
  const shown = all ? sources : sources.slice(0, 6);
  return (
    <div className="sources">
      <span className="sources-label">Sources</span>
      {shown.map((item, index) => (
        <a key={item.url} className="source-chip" href={item.url} target="_blank" rel="noreferrer" title={item.title}>
          <span className="source-no">{index + 1}</span>
          <span className="source-host">{host(item.url)}</span>
        </a>
      ))}
      {sources.length > 6 ? (
        <button type="button" className="source-more" onClick={() => setAll((value) => !value)}>
          {all ? "Fewer" : `+${sources.length - 6} more`}
        </button>
      ) : null}
    </div>
  );
}

function DraftCard({ draft, open, onOpen }: { draft: DraftInfo; open: boolean; onOpen: () => void }) {
  return (
    <button type="button" className={open ? "draft-card on" : "draft-card"} onClick={onOpen}>
      <span className="draft-card-icon">
        <FileText size={18} />
      </span>
      <span className="draft-card-body">
        <span className="draft-card-title">{draft.title}</span>
        <span className="draft-card-meta">
          Draft v{draft.version} · {draft.words.toLocaleString()} words · {draft.document_root ? "set in the template" : "not in the template yet"} · {when(draft.created_at)}
        </span>
      </span>
      <span className="draft-card-open">{open ? "Showing" : "Open"}</span>
    </button>
  );
}

function UsageLine({ usage }: { usage: NonNullable<Message["usage"]> }) {
  const parts = [
    `${(usage.input_tokens + usage.output_tokens).toLocaleString()} tokens`,
    usage.cached_tokens ? `${Math.round((usage.cached_tokens / Math.max(1, usage.input_tokens)) * 100)}% from cache` : "",
    usage.searches ? `${usage.searches} ${usage.searches === 1 ? "search" : "searches"}` : "",
    usage.rounds > 1 ? `${usage.rounds} rounds` : "",
  ].filter(Boolean);
  return <p className="usage-line">{parts.join(" · ")}</p>;
}

function host(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url.slice(0, 30);
  }
}

function looksLikeResearch(message: Message): boolean {
  if (isResearchBody(message.body)) return true;
  const steps = message.activity ?? [];
  return steps.some((step) => step.label === "Research" || step.parent === "research");
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className={done ? "copy done" : "copy"}
      aria-label={done ? "Copied" : "Copy the reply"}
      title={done ? "Copied" : "Copy"}
      onClick={() => {
        navigator.clipboard
          ?.writeText(text)
          .then(() => {
            setDone(true);
            window.setTimeout(() => setDone(false), 1400);
          })
          .catch(() => undefined);
      }}
    >
      {done ? <Check size={14} /> : <Copy size={14} />}
    </button>
  );
}

function UserText({ text }: { text: string }) {
  const [full, setFull] = useState(false);
  const lines = text.split("\n");
  const long = text.length > 700 || lines.length > 12;
  if (!long || full) {
    return (
      <div className="user-text">
        <p>{text}</p>
        {long ? (
          <button type="button" className="text-button" onClick={() => setFull(false)}>
            Show less
          </button>
        ) : null}
      </div>
    );
  }
  const preview = lines.slice(0, 8).join("\n").slice(0, 520);
  return (
    <div className="user-text">
      <p className="clipped">{preview}</p>
      <button type="button" className="text-button" onClick={() => setFull(true)}>
        Show all {lines.length} lines
      </button>
    </div>
  );
}

function DocCard({ doc, open, onOpen }: { doc: DocumentInfo; open: boolean; onOpen: () => void }) {
  const [loaded, setLoaded] = useState(false);
  return (
    <div className={open ? "doc-card on" : "doc-card"}>
      <button type="button" className="doc-thumb" onClick={onOpen} aria-label={`Open ${doc.title}`}>
        <img src={api.pageUrl(doc.id, 1, doc)} alt="" loading="lazy" className={loaded ? "loaded" : undefined} onLoad={() => setLoaded(true)} />
      </button>
      <div className="doc-body">
        <button type="button" className="doc-title" onClick={onOpen}>
          {doc.title}
        </button>
        <p className="doc-meta">
          <span>{pages(doc.pages)}</span>
          <span className={doc.spark_content ? "badge" : "badge plain"}>{doc.spark_content ? "Client pages" : "Internal"}</span>
          {doc.version && doc.version > 1 ? <span className="badge plain">Version {doc.version}</span> : null}
        </p>
        <div className="doc-actions">
          <button type="button" className={open ? "small-button" : "small-button primary"} onClick={onOpen} aria-pressed={open}>
            <Pages size={15} />
            {open ? "Showing" : "Open pages"}
          </button>
          <a className="small-button" href={api.downloadUrl(doc.id, "pdf")}>
            <Download size={15} />
            PDF
          </a>
          <a className="small-button" href={api.downloadUrl(doc.id, "docx")}>
            <Download size={15} />
            Word
          </a>
        </div>
      </div>
    </div>
  );
}

function Skeleton() {
  return (
    <div className="skeleton" aria-label="Loading the chat">
      <div className="sk-line user" />
      <div className="sk-block">
        <div className="sk-line short" />
        <div className="sk-line" />
        <div className="sk-card" />
      </div>
      <div className="sk-line user narrow" />
    </div>
  );
}
