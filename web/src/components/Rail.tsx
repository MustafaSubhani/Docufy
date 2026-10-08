import { useMemo, useState } from "react";
import { api, type ChatSummary, type Models } from "../api";
import { Folder, Gear, Kebab, Mark, PanelLeft, Plus, Search } from "../icons";
import { groupChats, useDismiss, when, type View } from "../lib";

type Props = {
  open: boolean;
  chats: ChatSummary[];
  query: string;
  onQuery: (value: string) => void;
  active: string;
  view: View;
  models: Models | null;
  onNew: () => void;
  onOpen: (id: string) => void;
  onNavigate: (hash: string) => void;
  onRename: (id: string, title: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onHide: () => void;
  onPalette: () => void;
};

export function Rail(props: Props) {
  const groups = useMemo(() => groupChats(props.chats), [props.chats]);
  const local = props.models?.options.find((item) => item.id === "dev");
  const cloud = props.models?.options.filter((item) => (item.id === "claude" || item.id === "openai") && item.available) ?? [];

  return (
    <aside className={props.open ? "rail open" : "rail"} aria-label="Chats">
      <div className="brand">
        <Mark />
        <span className="brand-name">Docufy</span>
        <span className="spacer" />
        <button type="button" className="icon-button" aria-label="Hide the sidebar" title="Hide the sidebar" onClick={props.onHide}>
          <PanelLeft size={18} />
        </button>
      </div>
      <button className="new-chat" type="button" onClick={props.onNew}>
        <Plus size={17} />
        New chat
      </button>
      <label className="search">
        <Search size={16} />
        <span className="visually-hidden">Search chats</span>
        <input value={props.query} onChange={(event) => props.onQuery(event.target.value)} placeholder="Search chats" />
        <button type="button" className="kbd-button" onClick={props.onPalette} title="Find anything">
          <kbd>Ctrl K</kbd>
        </button>
      </label>
      <nav className="history">
        {groups.length === 0 ? (
          <p className="rail-empty">{props.query ? "No chats match that search." : "Your chats will appear here."}</p>
        ) : (
          groups.map((group) => (
            <section key={group.label}>
              <h2>{group.label}</h2>
              <ul>
                {group.items.map((item, index) => (
                  <ChatRow
                    delay={Math.min(index, 8) * 18}
                    key={item.id}
                    chat={item}
                    active={item.id === props.active}
                    onOpen={() => props.onOpen(item.id)}
                    onRename={(title) => props.onRename(item.id, title)}
                    onDelete={() => props.onDelete(item.id)}
                  />
                ))}
              </ul>
            </section>
          ))
        )}
      </nav>
      <div className="rail-foot">
        <button className={props.view === "files" ? "nav on" : "nav"} type="button" onClick={() => props.onNavigate("#/files")}>
          <Folder size={17} />
          Files
        </button>
        <button className={props.view === "settings" ? "nav on" : "nav"} type="button" onClick={() => props.onNavigate("#/settings")}>
          <Gear size={17} />
          Settings
        </button>
        <p className="status-line">
          <i className={local?.available ? "dot ok" : "dot"} />
          {local ? (local.available ? "Local model ready" : "Local model offline") : "Checking models"}
          {cloud.length ? <span>{cloud.map((item) => item.label).join(" and ")} ready</span> : null}
        </p>
      </div>
    </aside>
  );
}

function ChatRow({
  delay,
  chat,
  active,
  onOpen,
  onRename,
  onDelete,
}: {
  delay: number;
  chat: ChatSummary;
  active: boolean;
  onOpen: () => void;
  onRename: (title: string) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const [menu, setMenu] = useState(false);
  const [editing, setEditing] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [title, setTitle] = useState(chat.title);
  const ref = useDismiss<HTMLDivElement>(menu, () => {
    setMenu(false);
    setConfirm(false);
  });

  if (editing) {
    return (
      <li className="chat-row">
        <form
          className="rename"
          onSubmit={(event) => {
            event.preventDefault();
            onRename(title).finally(() => setEditing(false));
          }}
        >
          <input
            autoFocus
            value={title}
            aria-label="Chat name"
            onChange={(event) => setTitle(event.target.value)}
            onBlur={() => setEditing(false)}
            onKeyDown={(event) => {
              if (event.key === "Escape") setEditing(false);
            }}
          />
        </form>
      </li>
    );
  }

  return (
    <li className={active ? "chat-row on" : "chat-row"} style={{ animationDelay: `${delay}ms` }}>
      <button className="chat" type="button" onClick={onOpen} title={chat.title}>
        <span className="chat-title">{chat.title}</span>
        <span className="chat-meta">
          {chat.busy ? <i className="dot busy" aria-label="Working" /> : null}
          <time>{when(chat.updated_at)}</time>
        </span>
      </button>
      <div className="row-menu" ref={ref}>
        <button className="icon-button" type="button" aria-label="Chat actions" aria-expanded={menu} onClick={() => setMenu((value) => !value)}>
          <Kebab size={16} />
        </button>
        {menu ? (
          <div className="popover" role="menu">
            {confirm ? (
              <>
                <p className="popover-note">Delete this chat and its documents?</p>
                <button type="button" role="menuitem" className="danger" onClick={() => onDelete().finally(() => setMenu(false))}>
                  Delete
                </button>
                <button type="button" role="menuitem" onClick={() => setConfirm(false)}>
                  Keep it
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setTitle(chat.title);
                    setEditing(true);
                    setMenu(false);
                  }}
                >
                  Rename
                </button>
                <a role="menuitem" href={api.exportUrl(chat.id)} onClick={() => setMenu(false)}>
                  Export
                </a>
                <button type="button" role="menuitem" className="danger" onClick={() => setConfirm(true)}>
                  Delete
                </button>
              </>
            )}
          </div>
        ) : null}
      </div>
    </li>
  );
}
