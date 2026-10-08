import { useEffect, useMemo, useRef, useState, type ComponentType } from "react";
import type { ChatSummary } from "../api";
import { Folder, Gear, Lock, Plus, Search } from "../icons";
import { when } from "../lib";

type Item = { id: string; label: string; hint: string; group: string; icon?: ComponentType<{ size?: number }>; run: () => void };

/** Ctrl or Cmd with K. Jump to a chat, a page, or a settings tab from the keyboard. */
export function Palette({ chats, onClose, go, onNew }: { chats: ChatSummary[]; onClose: () => void; go: (hash: string) => void; onNew: () => void }) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);

  const items = useMemo<Item[]>(() => {
    const actions: Item[] = [
      { id: "new", label: "New chat", hint: "Start a document", group: "Actions", icon: Plus, run: onNew },
      { id: "files", label: "Files", hint: "Every document", group: "Actions", icon: Folder, run: () => go("#/files") },
      { id: "settings", label: "Settings", hint: "General", group: "Actions", icon: Gear, run: () => go("#/settings/general") },
      { id: "keys", label: "Models and keys", hint: "Settings", group: "Actions", icon: Lock, run: () => go("#/settings/models") },
      { id: "usage", label: "Usage and budget", hint: "Settings", group: "Actions", icon: Gear, run: () => go("#/settings/usage") },
      { id: "data", label: "Backups and import", hint: "Settings", group: "Actions", icon: Gear, run: () => go("#/settings/data") },
      { id: "system", label: "Setup check", hint: "Settings", group: "Actions", icon: Gear, run: () => go("#/settings/system") },
    ];
    const chatItems: Item[] = chats.map((chat) => ({
      id: chat.id,
      label: chat.title,
      hint: when(chat.updated_at),
      group: "Chats",
      run: () => go(`#/c/${chat.id}`),
    }));
    const needle = query.trim().toLowerCase();
    const all = [...actions, ...chatItems];
    if (!needle) return [...actions.slice(0, 3), ...chatItems.slice(0, 7)];
    return all.filter((item) => item.label.toLowerCase().includes(needle)).slice(0, 12);
  }, [chats, query, go, onNew]);

  useEffect(() => input.current?.focus(), []);
  useEffect(() => setIndex(0), [query]);
  useEffect(() => {
    list.current?.querySelector(`[data-index="${index}"]`)?.scrollIntoView({ block: "nearest" });
  }, [index]);

  function pick(item: Item | undefined) {
    if (!item) return;
    onClose();
    item.run();
  }

  let lastGroup = "";
  return (
    <div className="palette-scrim" onMouseDown={onClose}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Find anything" onMouseDown={(event) => event.stopPropagation()}>
        <label className="palette-search">
          <Search size={17} />
          <input
            ref={input}
            value={query}
            placeholder="Find a chat or go to a page"
            aria-label="Find a chat or go to a page"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setIndex((value) => Math.min(items.length - 1, value + 1));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setIndex((value) => Math.max(0, value - 1));
              } else if (event.key === "Enter") {
                event.preventDefault();
                pick(items[index]);
              } else if (event.key === "Escape") {
                onClose();
              }
            }}
          />
          <kbd>Esc</kbd>
        </label>
        <ul className="palette-list" ref={list} role="listbox">
          {items.length === 0 ? <li className="palette-empty">Nothing matches that.</li> : null}
          {items.map((item, position) => {
            const heading = item.group !== lastGroup ? item.group : "";
            lastGroup = item.group;
            const Icon = item.icon;
            return (
              <li key={`${item.group}-${item.id}`}>
                {heading ? <p className="palette-group">{heading}</p> : null}
                <button
                  type="button"
                  role="option"
                  aria-selected={position === index}
                  data-index={position}
                  className={position === index ? "palette-item on" : "palette-item"}
                  onMouseEnter={() => setIndex(position)}
                  onClick={() => pick(item)}
                >
                  {Icon ? <Icon size={16} /> : <span className="palette-dot" />}
                  <span className="palette-label">{item.label}</span>
                  <span className="palette-hint">{item.hint}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
