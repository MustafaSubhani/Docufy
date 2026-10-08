import { useEffect, useMemo, useRef, useState } from "react";
import { api, type ChatDetail, type DocumentDetail, type DocumentInfo, type DocumentSection } from "../api";
import { ArrowUp, Check, ChevronDown, Close, Download, Minus, Pen, Plus, Refresh, Spinner } from "../icons";
import { pages, useDismiss, when } from "../lib";

const QUICK = [
  ["Shorten", (where: string) => `Shorten ${where}. Keep every fact and figure.`],
  ["Add detail", (where: string) => `Add useful, specific detail to ${where}. Do not invent prices, dates or names; mark gaps to be confirmed.`],
  ["Add a diagram", (where: string) => `Add or improve one mermaid diagram in ${where} that shows the flow or architecture.`],
  ["Make it a table", (where: string) => `Restructure the key points of ${where} into a clear table where that reads better.`],
  ["More formal", (where: string) => `Make the tone of ${where} more formal and confident for a client audience.`],
] as const;

const ZOOMS = [0.6, 0.75, 0.9, 1, 1.2];
/** The brand pack's pages. They come from the template, so they are not offered for editing. */
const COMPANY = /\b(who we are|why choose|about spark|solution areas|how we move forward|contact)\b/i;

type Props = {
  doc: DocumentInfo;
  chat: ChatDetail;
  busy: boolean;
  onPick: (doc: DocumentInfo) => void;
  onApplyEdit: (text: string, sectionIds: string[]) => Promise<boolean>;
  onChat: (detail: ChatDetail) => void;
};

export function Preview({ doc, chat, busy, onPick, onApplyEdit, onChat }: Props) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [current, setCurrent] = useState(1);
  const [zoom, setZoom] = useState(2);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [source, setSource] = useState<string | null>(null);
  const [original, setOriginal] = useState("");
  const [note, setNote] = useState("");
  const desk = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const count = Math.max(1, doc.pages || 1);
  const sections = useMemo(
    () => (detail?.sections ?? []).filter((item) => item.label && !COMPANY.test(`${item.kicker} ${item.label}`)),
    [detail?.sections],
  );

  useEffect(() => {
    let stop = false;
    api.document(doc.id).then((data) => !stop && setDetail(data)).catch(() => undefined);
    setCurrent(1);
    setSelected([]);
    setSource(null);
    desk.current?.scrollTo({ top: 0 });
    return () => {
      stop = true;
    };
  }, [doc.id]);

  useEffect(() => {
    const root = desk.current;
    if (!root) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const seen = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (seen) setCurrent(Number((seen.target as HTMLElement).dataset.page || 1));
      },
      { root, threshold: [0.35, 0.6] },
    );
    root.querySelectorAll("[data-page]").forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [doc.id, count, zoom, source]);

  const versions = detail?.versions ?? [];
  const newest = versions.length ? versions[versions.length - 1] : null;
  const isOld = Boolean(newest && newest.id !== doc.id);
  const onPage = (page: number) => sections.filter((section) => section.pages.includes(page));
  const chosen = sections.filter((section) => selected.includes(section.id));
  const where = chosen.length ? (chosen.length === 1 ? `the ${chosen[0].label} section` : "the selected sections") : `the section on page ${current}`;

  function toggle(id: string) {
    setSelected((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  }

  function pickPage(page: number) {
    const ids = onPage(page).map((item) => item.id);
    if (!ids.length) return;
    const all = ids.every((id) => selected.includes(id));
    setSelected((prev) => (all ? prev.filter((id) => !ids.includes(id)) : Array.from(new Set([...prev, ...ids]))));
    input.current?.focus();
  }

  async function run(value: string) {
    const trimmed = value.trim();
    if (!trimmed || sending || busy) return;
    setSending(true);
    const ids = selected.length ? selected : onPage(current).map((item) => item.id);
    const scoped = selected.length ? trimmed : `On page ${current} of the open document: ${trimmed}`;
    const ok = await onApplyEdit(scoped, ids);
    setSending(false);
    if (ok) setText("");
  }

  async function openSource() {
    setNote("");
    try {
      const data = await api.documentSource(doc.id);
      setSource(data.markdown);
      setOriginal(data.markdown);
    } catch (exc) {
      setNote(exc instanceof Error ? exc.message : "The source did not load.");
    }
  }

  async function saveSource() {
    if (source === null) return;
    setSending(true);
    try {
      onChat(await api.saveDocumentSource(doc.id, source));
      setSource(null);
    } catch (exc) {
      setNote(exc instanceof Error ? exc.message : "The edit was not saved.");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="pages-view">
      <header className="preview-head">
        <div className="preview-title">
          <h2>{doc.title}</h2>
          <p>
            <span>{pages(count)}</span>
            <span className={doc.spark_content ? "badge" : "badge plain"}>{doc.spark_content ? "Client pages" : "Internal"}</span>
            {versions.length > 1 ? (
              <label className="version-select">
                <span className="visually-hidden">Version</span>
                <select
                  value={doc.id}
                  onChange={(event) => {
                    const picked = versions.find((item) => item.id === event.target.value);
                    const known = chat.documents.find((item) => item.id === event.target.value);
                    if (picked) onPick(known ?? { ...doc, id: picked.id, version: picked.version, pages: picked.pages });
                  }}
                >
                  {[...versions].reverse().map((item) => (
                    <option key={item.id} value={item.id}>
                      Version {item.version}, {when(item.created_at)}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {isOld ? (
              <button
                type="button"
                className="link-button"
                onClick={() =>
                  api
                    .restoreVersion(doc.id)
                    .then(onChat)
                    .catch((exc: Error) => setNote(exc.message))
                }
              >
                <Refresh size={13} /> Restore this version
              </button>
            ) : null}
          </p>
        </div>
        <div className="preview-actions">
          {source === null ? (
            <button type="button" className="small-button" onClick={openSource} title="Edit the template source by hand">
              <Pen size={14} />
              Source
            </button>
          ) : null}
          <DownloadMenu id={doc.id} />
        </div>
      </header>
      {note ? (
        <p className="preview-note" role="alert">
          {note}
          <button type="button" className="icon-button" aria-label="Dismiss" onClick={() => setNote("")}>
            <Close size={14} />
          </button>
        </p>
      ) : null}
      {source !== null ? (
        <div className="source-editor">
          <p className="source-help">
            This is the Spark template Markdown. Keep the front matter, the <code># 01 Title</code> kickers and the <code>:::block</code> fences. Saving sets the pages again as a new version.
          </p>
          <textarea value={source} spellCheck={false} onChange={(event) => setSource(event.target.value)} aria-label="Template source" />
          <div className="source-actions">
            <button type="button" className="small-button" onClick={() => setSource(null)}>
              Cancel
            </button>
            <button type="button" className="small-button primary" disabled={sending || source === original} onClick={saveSource}>
              {sending ? "Saving" : "Save as a new version"}
            </button>
          </div>
        </div>
      ) : (
        <div className={busy ? "desk working" : "desk"} ref={desk} style={{ ["--zoom" as string]: String(ZOOMS[zoom]) }}>
          {busy ? (
            <div className="desk-busy" role="status">
              <Spinner size={13} />
              Making the next version
            </div>
          ) : null}
          {Array.from({ length: count }, (_, index) => {
            const page = index + 1;
            const here = onPage(page);
            const picked = here.length > 0 && here.every((item) => selected.includes(item.id));
            return (
              <figure
                key={`${doc.id}-${index}`}
                className={picked ? "sheet picked" : "sheet"}
                data-page={page}
                style={{ animationDelay: `${Math.min(index, 3) * 60}ms` }}
              >
                <img
                  src={api.pageUrl(doc.id, page, doc)}
                  alt={`Page ${page} of ${count}`}
                  loading={index < 2 ? "eager" : "lazy"}
                  onLoad={(event) => event.currentTarget.classList.add("loaded")}
                />
                {here.length ? (
                  <button type="button" className="sheet-pick" onClick={() => pickPage(page)} aria-pressed={picked}>
                    {picked ? <Check size={13} /> : <Pen size={13} />}
                    {picked ? "Selected" : here.length === 1 ? `Edit ${here[0].label}` : `Edit this page`}
                  </button>
                ) : null}
                <span className="sheet-no">{page}</span>
              </figure>
            );
          })}
        </div>
      )}
      {source === null ? (
        <div className="ask-dock pages">
          {chosen.length ? (
            <div className="ask-scope">
              {chosen.map((section) => (
                <SectionChip key={section.id} section={section} onRemove={() => toggle(section.id)} />
              ))}
              <button type="button" className="link-button" onClick={() => setSelected([])}>
                Clear
              </button>
            </div>
          ) : sections.length ? (
            <SectionMenu sections={sections} selected={selected} onToggle={toggle} current={current} />
          ) : null}
          <div className="ask-chips">
            {QUICK.map(([label, make]) => (
              <button key={label} type="button" className="ask-chip" disabled={busy || sending} onClick={() => run(make(where))}>
                {label}
              </button>
            ))}
          </div>
          <form
            className="ask-form"
            onSubmit={(event) => {
              event.preventDefault();
              run(text);
            }}
          >
            <textarea
              ref={input}
              rows={1}
              value={text}
              placeholder={chosen.length ? `Change ${where}` : `Ask for a change, e.g. update the commercial table on page ${current}`}
              aria-label="Describe the change"
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  run(text);
                }
              }}
            />
            <button type="submit" className="send small" disabled={busy || sending || !text.trim()} aria-label="Apply the change">
              {sending ? <Spinner size={13} /> : <ArrowUp size={16} />}
            </button>
          </form>
        </div>
      ) : null}
      <footer className="preview-foot">
        <span>
          Page {current} of {count}
        </span>
        <div className="zoom" role="group" aria-label="Zoom">
          <button type="button" className="icon-button" aria-label="Smaller" disabled={zoom === 0} onClick={() => setZoom((value) => Math.max(0, value - 1))}>
            <Minus size={16} />
          </button>
          <span>{Math.round(ZOOMS[zoom] * 100)}%</span>
          <button type="button" className="icon-button" aria-label="Larger" disabled={zoom === ZOOMS.length - 1} onClick={() => setZoom((value) => Math.min(ZOOMS.length - 1, value + 1))}>
            <Plus size={16} />
          </button>
        </div>
      </footer>
    </div>
  );
}

function SectionChip({ section, onRemove }: { section: DocumentSection; onRemove: () => void }) {
  return (
    <span className="scope-chip">
      <span className="scope-kicker">{section.kicker.split(" ")[0]}</span>
      {section.label}
      <button type="button" aria-label={`Remove ${section.label}`} onClick={onRemove}>
        <Close size={12} />
      </button>
    </span>
  );
}

function SectionMenu({ sections, selected, onToggle, current }: { sections: DocumentSection[]; selected: string[]; onToggle: (id: string) => void; current: number }) {
  const [open, setOpen] = useState(false);
  const ref = useDismiss<HTMLDivElement>(open, () => setOpen(false));
  return (
    <div className="section-menu" ref={ref}>
      <button type="button" className="link-button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        Choose sections <ChevronDown size={13} />
      </button>
      {open ? (
        <div className="popover section-pop" role="listbox" aria-label="Sections">
          {sections.map((section) => (
            <label key={section.id} className={section.pages.includes(current) ? "section-row here" : "section-row"}>
              <input type="checkbox" checked={selected.includes(section.id)} onChange={() => onToggle(section.id)} />
              <span className="section-row-label">
                {section.kicker ? <span className="scope-kicker">{section.kicker.split(" ")[0]}</span> : null}
                {section.label}
              </span>
              {section.pages.length ? <span className="section-row-pages">p{section.pages[0]}</span> : null}
            </label>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function DownloadMenu({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const ref = useDismiss<HTMLDivElement>(open, () => setOpen(false));
  return (
    <div className="download-menu" ref={ref}>
      <button type="button" className="small-button primary" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <Download size={15} />
        Download
        <ChevronDown size={14} />
      </button>
      {open ? (
        <div className="popover right" role="menu">
          <a role="menuitem" href={api.downloadUrl(id, "pdf")} onClick={() => setOpen(false)}>
            PDF
            <span>For sending</span>
          </a>
          <a role="menuitem" href={api.downloadUrl(id, "docx")} onClick={() => setOpen(false)}>
            Word
            <span>For editing in Office</span>
          </a>
          <a role="menuitem" href={api.downloadUrl(id, "md")} onClick={() => setOpen(false)}>
            Markdown
            <span>The template source</span>
          </a>
        </div>
      ) : null}
    </div>
  );
}
