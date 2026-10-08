import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { api, type ChatDetail, type DocumentInfo, type Message, type ModelChoice, type Models } from "../api";
import { ArrowDown, Check, Copy, Download, FileText, Layout, List, Mark, Pages, Pen, Refresh, Upload } from "../icons";
import { pages, Rich } from "../lib";
import { ResearchReply } from "./ResearchReply";
import { isResearchBody } from "../openui/extract";
import { Activity } from "./Activity";
import { Composer, type ComposerHandle } from "./Composer";

const STARTERS = [
  { icon: Pen, title: "Write a proposal", note: "Describe the client and the work. Docufy writes each section.", text: "Write a client proposal for ", spark: true },
  { icon: List, title: "Draft a scope of work", note: "Deliverables, timeline, and what the client provides", text: "Draft a scope of work for ", spark: true },
  { icon: Layout, title: "Set my notes as pages", note: "Paste notes with headings and keep every word", text: "# ", spark: true },
  { icon: FileText, title: "Internal report", note: "Clean pages without the company sections", text: "Write an internal report on ", spark: false },
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
            <p>Describe a document and Docufy writes it, or paste notes and it sets them as pages in the Spark Technologies template.</p>
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
            {props.loading ? (
              <Skeleton />
            ) : (
              messages.map((message) => (
                <MessageView
                  key={message.id}
                  message={message}
                  chat={props.chat!}
                  models={props.models}
                  openId={props.preview?.id ?? ""}
                  onOpen={props.onOpen}
                  onStop={() => props.onStop(message.id)}
                  onRetry={(model) => props.onRetry(message.id, model)}
                />
              ))
            )}
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
  onOpen,
  onStop,
  onRetry,
}: {
  message: Message;
  chat: ChatDetail;
  models: Models | null;
  openId: string;
  onOpen: (doc: DocumentInfo) => void;
  onStop: () => void;
  onRetry: (model?: ModelChoice) => void;
}) {
  if (message.role === "user") {
    const target = chat.documents.find((item) => item.id === message.target_id);
    return (
      <article className="bubble user">
        {message.attachment ? (
          <span className="attachment">
            <FileText size={15} />
            {message.attachment}
          </span>
        ) : null}
        {target ? (
          <span className="target">
            <Pen size={13} />
            Change to {target.title}
            {target.version && target.version > 1 ? ` v${target.version}` : ""}
          </span>
        ) : null}
        {message.web ? <span className="target">Web</span> : null}
        {message.body && message.body !== message.attachment ? <UserText text={message.body} /> : null}
      </article>
    );
  }

  const doc = chat.documents.find((item) => item.id === message.document_id);
  const live = message.status === "writing";
  const streaming = live && message.progress?.streaming;
  const steps = message.activity ?? [];
  const usable = (models?.options ?? []).filter((item) => item.id !== "off" && item.available);

  return (
    <article className="bubble assistant">
      <header className="who">
        <Mark size={22} />
        <span>Docufy</span>
        {message.model_label ? <span className="who-model">{message.model_label}</span> : null}
        {!live && message.body ? <CopyButton text={message.body} /> : null}
      </header>
      {live && !streaming ? (
        <Activity steps={steps} live fallback={message.progress?.text || message.body} onStop={onStop} />
      ) : steps.length ? (
        <Activity steps={steps} live={false} fallback="" outcome={message.status} />
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
      ) : !live || streaming ? (
        <div className={message.status === "failed" ? "reply failed" : message.status === "stopped" ? "reply muted" : "reply"}>
          {looksLikeResearch(message) ? (
            <ResearchReply text={message.body} streaming={streaming} />
          ) : (
            <Rich text={message.body} />
          )}
          {streaming ? <span className="caret" /> : null}
        </div>
      ) : null}
      {message.status === "failed" || message.status === "stopped" ? (
        <button type="button" className="ghost-button retry" onClick={() => onRetry()}>
          <Refresh size={15} />
          Try again
        </button>
      ) : null}
      {doc ? <DocCard doc={doc} open={doc.id === openId} onOpen={() => onOpen(doc)} /> : null}
    </article>
  );
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
