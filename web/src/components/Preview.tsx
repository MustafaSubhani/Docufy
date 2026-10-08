import { useEffect, useMemo, useRef, useState } from "react";
import { api, type DocumentDetail, type DocumentInfo } from "../api";
import { ChevronDown, Close, Download, Minus, Plus } from "../icons";
import { pages, useDismiss, when } from "../lib";

function toolText(label: string, scoped: boolean) {
  const where = scoped ? "the selected section(s)" : "the section on this page";
  switch (label) {
    case "Shorten":
      return `Shorten ${where}. Keep every fact.`;
    case "Add detail":
      return `Add more useful detail to ${where}. Do not invent prices or dates.`;
    case "Add diagram":
      return `Add or improve one mermaid diagram in ${where}.`;
    case "Fix tables":
      return `Make the tables in ${where} clearer without changing the meaning.`;
    default:
      return "";
  }
}

const QUICK_LABELS = ["Shorten", "Add detail", "Add diagram", "Fix tables"] as const;

const ZOOMS = [0.7, 0.85, 1, 1.2];

export function Preview({
  doc,
  onPick,
  onClose,
  onResize,
  onApplyEdit,
}: {
  doc: DocumentInfo;
  onPick: (doc: DocumentInfo) => void;
  onClose: () => void;
  onResize: (width: number) => void;
  onApplyEdit: (text: string, page: number, sectionIds: string[]) => Promise<boolean>;
}) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [current, setCurrent] = useState(1);
  const [zoom, setZoom] = useState(2);
  const [editText, setEditText] = useState("");
  const [editBusy, setEditBusy] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const desk = useRef<HTMLDivElement>(null);
  const count = Math.max(1, doc.pages || 1);
  const sections = detail?.sections ?? [];

  useEffect(() => {
    let stop = false;
    api.document(doc.id).then((data) => !stop && setDetail(data)).catch(() => undefined);
    setCurrent(1);
    setSelected([]);
    desk.current?.scrollTo({ top: 0 });
    return () => {
      stop = true;
    };
  }, [doc.id]);

  const onPage = useMemo(
    () => sections.filter((section) => section.pages.includes(current)).map((section) => section.id),
    [sections, current],
  );

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
  }, [doc.id, count, zoom]);

  const versions = detail?.versions ?? [];
  const scoped = selected.length > 0;

  function toggleSection(id: string) {
    setSelected((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  }

  function selectOnPage() {
    if (!onPage.length) return;
    setSelected(onPage);
  }

  function runEdit(text: string) {
    if (!text.trim() || editBusy) return;
    setEditBusy(true);
    onApplyEdit(text, current, selected)
      .then((ok) => {
        if (ok) setEditText("");
      })
      .finally(() => setEditBusy(false));
  }

  return (
    <aside className="preview" aria-label="Document preview">
      <div
        className="resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the preview"
        onPointerDown={(event) => {
          event.preventDefault();
          const handle = event.currentTarget;
          handle.setPointerCapture(event.pointerId);
          document.body.classList.add("resizing");
          const move = (next: PointerEvent) => onResize(window.innerWidth - next.clientX);
          const up = () => {
            document.body.classList.remove("resizing");
            handle.removeEventListener("pointermove", move);
            handle.removeEventListener("pointerup", up);
          };
          handle.addEventListener("pointermove", move);
          handle.addEventListener("pointerup", up);
        }}
        onDoubleClick={() => onResize(0)}
      />
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
                    if (picked) onPick({ ...doc, id: picked.id, version: picked.version, pages: picked.pages });
                  }}
                >
                  {versions.map((item) => (
                    <option key={item.id} value={item.id}>
                      Version {item.version}, {when(item.created_at)}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
          </p>
        </div>
        <div className="preview-actions">
          <DownloadMenu id={doc.id} />
          <button type="button" className="icon-button" aria-label="Close the preview" onClick={onClose}>
            <Close />
          </button>
        </div>
      </header>
      <div className="desk" ref={desk} style={{ ["--zoom" as string]: String(ZOOMS[zoom]) }}>
        {Array.from({ length: count }, (_, index) => (
          <figure key={`${doc.id}-${index}`} className="sheet" data-page={index + 1} style={{ animationDelay: `${Math.min(index, 3) * 60}ms` }}>
            <img
              src={api.pageUrl(doc.id, index + 1, doc)}
              alt={`Page ${index + 1} of ${count}`}
              loading={index < 2 ? "eager" : "lazy"}
              onLoad={(event) => event.currentTarget.classList.add("loaded")}
            />
          </figure>
        ))}
      </div>
      <div className="preview-edit">
        <p className="preview-edit-title">Edit this document</p>
        {sections.length ? (
          <div className="preview-sections" role="group" aria-label="Sections to edit">
            <div className="preview-sections-head">
              <span>Sections</span>
              <div className="preview-sections-actions">
                <button type="button" className="link-button" disabled={!onPage.length} onClick={selectOnPage}>
                  On this page
                </button>
                <button type="button" className="link-button" disabled={!selected.length} onClick={() => setSelected([])}>
                  Clear
                </button>
              </div>
            </div>
            <ul className="preview-section-list">
              {sections.map((section) => {
                const active = selected.includes(section.id);
                const onCurrentPage = section.pages.includes(current);
                return (
                  <li key={section.id}>
                    <label className={onCurrentPage ? "preview-section on-page" : "preview-section"}>
                      <input
                        type="checkbox"
                        checked={active}
                        onChange={() => toggleSection(section.id)}
                      />
                      <span className="preview-section-label">{section.label}</span>
                      {section.pages.length ? (
                        <span className="preview-section-pages">p{section.pages.join(", p")}</span>
                      ) : null}
                    </label>
                  </li>
                );
              })}
            </ul>
            <p className="preview-section-hint">
              {scoped
                ? `${selected.length} section${selected.length === 1 ? "" : "s"} selected. Edits apply only there.`
                : "Select one or more sections, or leave none selected to scope by the current page."}
            </p>
          </div>
        ) : null}
        <form
          className="preview-edit-form"
          onSubmit={(event) => {
            event.preventDefault();
            runEdit(editText);
          }}
        >
          <textarea
            rows={2}
            value={editText}
            placeholder="Describe the change, for example: expand the data requirements table"
            aria-label="Edit instruction"
            onChange={(event) => setEditText(event.target.value)}
          />
          <button type="submit" className="small-button primary" disabled={editBusy || !editText.trim()}>
            Apply
          </button>
        </form>
        <div className="preview-tools" role="group" aria-label="Quick edits">
          {QUICK_LABELS.map((label) => (
            <button key={label} type="button" className="small-button" disabled={editBusy} onClick={() => runEdit(toolText(label, scoped))}>
              {label}
            </button>
          ))}
        </div>
      </div>
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
    </aside>
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
            <span>The source Docufy sets</span>
          </a>
        </div>
      ) : null}
    </div>
  );
}
