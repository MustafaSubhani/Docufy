import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { DocumentInfo, ModelChoice, Models } from "../api";
import { ArrowUp, Check, ChevronDown, Chip, Close, Cloud, FileText, Paperclip, Pencil, StopSquare, Type } from "../icons";
import { bytes, go, useDismiss } from "../lib";

export const ACCEPT = ".md,.markdown,.txt,.pdf,.docx,.pptx,.csv,.xlsx";

export type ComposerHandle = { fill: (text: string) => void; attach: (file: File) => void; focus: () => void };

type Props = {
  busy: boolean;
  spark: boolean;
  onSpark: (value: boolean) => void;
  web: boolean;
  onWeb: (value: boolean) => void;
  model: ModelChoice;
  models: Models | null;
  onModel: (value: ModelChoice) => void;
  target: DocumentInfo | null;
  editing: boolean;
  onEditing: (value: boolean) => void;
  onSend: (text: string, file: File | null) => Promise<boolean>;
  onStop: () => void;
};

export const Composer = forwardRef<ComposerHandle, Props>(function Composer(props, handle) {
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const editingDoc = props.editing && props.target && !file ? props.target : null;

  useImperativeHandle(handle, () => ({
    fill: (value: string) => {
      setText(value);
      window.setTimeout(() => {
        const area = areaRef.current;
        if (!area) return;
        area.focus();
        area.setSelectionRange(value.length, value.length);
      }, 0);
    },
    attach: (next: File) => setFile(next),
    focus: () => areaRef.current?.focus(),
  }));

  useEffect(() => {
    const area = areaRef.current;
    if (!area) return;
    area.style.height = "0px";
    area.style.height = `${Math.min(area.scrollHeight, Math.round(window.innerHeight * 0.4))}px`;
  }, [text]);

  async function submit() {
    const value = text.trim();
    if ((!value && !file) || props.busy) return;
    const attached = file;
    setText("");
    setFile(null);
    const sent = await props.onSend(value, attached);
    if (!sent) {
      setText(value);
      setFile(attached);
    }
  }

  const placeholder = editingDoc
    ? "Say what to change, for example: replace ACME with Acme Fleet"
    : "Paste notes, describe a document, or ask a question";
  const researchHint =
    !editingDoc && props.model !== "off" && !props.web && /\b(research|look up|find out)\b/i.test(text.trim());

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        submit().catch(() => undefined);
      }}
      onPaste={(event) => {
        const pasted = event.clipboardData.files?.[0];
        if (pasted) {
          event.preventDefault();
          setFile(pasted);
        }
      }}
    >
      {file || editingDoc || (props.target && !props.editing) ? (
        <div className="composer-chips">
          {file ? (
            <span className="chip">
              <FileText size={15} />
              <span className="chip-text">{file.name}</span>
              <span className="chip-meta">{bytes(file.size)}</span>
              <button type="button" className="chip-x" aria-label="Remove the file" onClick={() => setFile(null)}>
                <Close size={14} />
              </button>
            </span>
          ) : null}
          {editingDoc ? (
            <span className="chip accent">
              <Pencil size={14} />
              <span className="chip-text">
                Changes go to {editingDoc.title}
                {editingDoc.version && editingDoc.version > 1 ? ` v${editingDoc.version}` : ""}
              </span>
              <button type="button" className="chip-x" aria-label="Write a new document instead" onClick={() => props.onEditing(false)}>
                <Close size={14} />
              </button>
            </span>
          ) : null}
          {props.target && !props.editing && !file ? (
            <button type="button" className="chip ghost" onClick={() => props.onEditing(true)}>
              <Pencil size={14} />
              Edit the open document
            </button>
          ) : null}
          {researchHint ? (
            <span className="chip ghost" title="Sending will search the web for this question">
              Research uses Web
            </span>
          ) : null}
        </div>
      ) : null}
      <textarea
        ref={areaRef}
        rows={1}
        value={text}
        placeholder={placeholder}
        aria-label="Message"
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            submit().catch(() => undefined);
          }
        }}
      />
      <div className="composer-bar">
        <button className="icon-button" type="button" aria-label="Attach a file" title="Attach a file" onClick={() => fileRef.current?.click()}>
          <Paperclip />
        </button>
        <input
          ref={fileRef}
          className="visually-hidden"
          type="file"
          accept={ACCEPT}
          tabIndex={-1}
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            event.target.value = "";
          }}
        />
        {!editingDoc ? (
          <div className="toggle" role="group" aria-label="Company pages">
            <button type="button" className={props.spark ? "on" : undefined} aria-pressed={props.spark} onClick={() => props.onSpark(true)}>
              Client
            </button>
            <button type="button" className={!props.spark ? "on" : undefined} aria-pressed={!props.spark} onClick={() => props.onSpark(false)}>
              Internal
            </button>
          </div>
        ) : null}
        <button
          type="button"
          className={props.web ? "web-toggle on" : "web-toggle"}
          aria-pressed={props.web}
          title="Search the web for this message"
          onClick={() => props.onWeb(!props.web)}
        >
          Web
        </button>
        <span className="spacer" />
        <ModelMenu models={props.models} value={props.model} onChange={props.onModel} />
        {props.busy ? (
          <button className="send stop" type="button" aria-label="Stop" title="Stop" onClick={props.onStop}>
            <StopSquare />
          </button>
        ) : (
          <button className="send" type="submit" aria-label="Send" title="Send" disabled={!text.trim() && !file}>
            <ArrowUp />
          </button>
        )}
      </div>
    </form>
  );
});

function ModelMenu({ models, value, onChange }: { models: Models | null; value: ModelChoice; onChange: (value: ModelChoice) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useDismiss<HTMLDivElement>(open, () => setOpen(false));
  const current = models?.options.find((item) => item.id === value);

  return (
    <div className="model-menu" ref={ref}>
      <button type="button" className="model-pill" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((state) => !state)}>
        <ModelIcon id={value} />
        <span>{current?.label ?? "Format only"}</span>
        <ChevronDown size={15} />
      </button>
      {open ? (
        <div className="popover model-list" role="listbox" aria-label="Model">
          <p className="popover-title">Model for this message</p>
          {(models?.options ?? []).map((item) => (
            <button
              key={item.id}
              type="button"
              role="option"
              aria-selected={item.id === value}
              disabled={!item.available}
              className={item.id === value ? "option on" : "option"}
              onClick={() => {
                onChange(item.id);
                setOpen(false);
              }}
            >
              <ModelIcon id={item.id} />
              <span className="option-text">
                <span className="option-label">{item.label}</span>
                <span className="option-detail">{item.available ? item.detail : item.reason}</span>
              </span>
              {item.id === value ? <Check size={16} /> : null}
            </button>
          ))}
          <button
            type="button"
            className="popover-link"
            onClick={() => {
              setOpen(false);
              go("#/settings/models");
            }}
          >
            Keys and local model settings
          </button>
        </div>
      ) : null}
    </div>
  );
}

function ModelIcon({ id }: { id: ModelChoice }) {
  if (id === "off") return <Type size={16} />;
  if (id === "dev") return <Chip size={16} />;
  return <Cloud size={16} />;
}
