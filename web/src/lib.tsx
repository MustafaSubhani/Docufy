import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import type { ChatSummary } from "./api";

export type View = "chat" | "files" | "settings";

export function useHash() {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const onChange = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

export function viewFrom(hash: string): View {
  if (hash.startsWith("#/files")) return "files";
  if (hash.startsWith("#/settings")) return "settings";
  return "chat";
}

export function settingsTab(hash: string) {
  const match = hash.match(/^#\/settings\/([a-z]+)$/);
  return match?.[1] ?? "general";
}

export function chatIdFrom(hash: string) {
  const match = hash.match(/^#\/c\/([a-f0-9]{12})$/);
  return match?.[1] ?? "";
}

export function go(next: string) {
  window.location.hash = next;
}

export function groupChats(chats: ChatSummary[]) {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const today = start.getTime();
  const day = 86_400_000;
  const buckets = [
    { label: "Today", from: today, to: Infinity },
    { label: "Yesterday", from: today - day, to: today },
    { label: "Previous 7 days", from: today - 7 * day, to: today - day },
    { label: "Previous 30 days", from: today - 30 * day, to: today - 7 * day },
    { label: "Older", from: 0, to: today - 30 * day },
  ];
  return buckets
    .map((bucket) => ({
      label: bucket.label,
      items: chats.filter((item) => {
        const time = item.updated_at * 1000;
        return time >= bucket.from && time < bucket.to;
      }),
    }))
    .filter((bucket) => bucket.items.length > 0);
}

export function when(ts: number) {
  const date = new Date(ts * 1000);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  const sameYear = date.getFullYear() === now.getFullYear();
  return date.toLocaleDateString([], sameYear ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" });
}

export function bytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function pages(count: number) {
  return count === 1 ? "1 page" : `${count} pages`;
}

export function useDismiss<T extends HTMLElement>(open: boolean, onClose: () => void) {
  const ref = useRef<T>(null);
  useEffect(() => {
    if (!open) return;
    function onDown(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose();
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);
  return ref;
}

export function useStored<T extends string>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => (window.localStorage.getItem(key) as T | null) ?? initial);
  function update(next: T) {
    setValue(next);
    window.localStorage.setItem(key, next);
  }
  return [value, update];
}

/** A small, safe Markdown subset for replies: paragraphs, lists, bold, and inline code. */
export function Rich({ text }: { text: string }) {
  const blocks = text.replace(/\r\n/g, "\n").split(/\n{2,}/);
  return (
    <>
      {blocks.map((block, index) => {
        const lines = block.split("\n").filter((line) => line.trim());
        if (!lines.length) return null;
        if (lines.every((line) => /^\s*[-*]\s+/.test(line))) {
          return (
            <ul key={index}>
              {lines.map((line, i) => <li key={i}>{inline(line.replace(/^\s*[-*]\s+/, ""))}</li>)}
            </ul>
          );
        }
        if (lines.every((line) => /^\s*\d+[.)]\s+/.test(line))) {
          return (
            <ol key={index}>
              {lines.map((line, i) => <li key={i}>{inline(line.replace(/^\s*\d+[.)]\s+/, ""))}</li>)}
            </ol>
          );
        }
        if (/^#{1,4}\s+/.test(lines[0]) && lines.length === 1) {
          return <p key={index} className="rich-heading">{inline(lines[0].replace(/^#{1,4}\s+/, ""))}</p>;
        }
        return (
          <p key={index}>
            {lines.map((line, i) => (
              <Fragment key={i}>
                {i > 0 ? <br /> : null}
                {inline(line)}
              </Fragment>
            ))}
          </p>
        );
      })}
    </>
  );
}

function inline(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\)|https?:\/\/[^\s<>)\]]+)/g);
  return parts.map((part, index) => {
    if (/^\*\*[^*]+\*\*$/.test(part)) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (/^`[^`]+`$/.test(part)) return <code key={index}>{part.slice(1, -1)}</code>;
    const labelled = part.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
    if (labelled?.[1] && labelled[2]) {
      return (
        <a key={index} href={labelled[2]} target="_blank" rel="noreferrer">{labelled[1]}</a>
      );
    }
    if (/^https?:\/\//.test(part)) {
      const url = part.replace(/[.,;:]+$/, "");
      return (
        <Fragment key={index}>
          <a href={url} target="_blank" rel="noreferrer">{url}</a>
          {part.slice(url.length)}
        </Fragment>
      );
    }
    return <Fragment key={index}>{part}</Fragment>;
  });
}
