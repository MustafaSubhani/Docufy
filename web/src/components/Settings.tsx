import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, type Backup, type KeyStatus, type Models, type Panel, type ReadyCheck, type Settings, type SettingsDraft, type Usage } from "../api";
import { Check, Close, Download, Upload } from "../icons";
import { bytes, go, when } from "../lib";

const TABS = [
  ["general", "General"],
  ["models", "Models and keys"],
  ["usage", "Usage"],
  ["privacy", "Privacy"],
  ["data", "Data"],
  ["system", "System"],
  ["memory", "Memory"],
] as const;

type Tab = (typeof TABS)[number][0];

type Props = {
  tab: string;
  settings: Settings;
  models: Models | null;
  onSave: (next: SettingsDraft) => Promise<void>;
  onImported: (chatId: string) => void;
};

export function SettingsPage(props: Props) {
  const tab = (TABS.some(([id]) => id === props.tab) ? props.tab : "general") as Tab;
  const [draft, setDraft] = useState<Settings>(props.settings);
  const [password, setPassword] = useState("");
  const [clearPassword, setClearPassword] = useState(false);
  const [notice, setNotice] = useState("");
  const [usage, setUsage] = useState<Usage | null>(null);
  const [panel, setPanel] = useState<Panel | null>(null);

  useEffect(() => setDraft(props.settings), [props.settings]);
  useEffect(() => {
    api.usage().then(setUsage).catch(() => undefined);
    api.panel().then(setPanel).catch(() => undefined);
  }, []);

  const dirty = useMemo(
    () => password.trim() !== "" || clearPassword || JSON.stringify({ ...draft, has_password: undefined }) !== JSON.stringify({ ...props.settings, has_password: undefined }),
    [draft, props.settings, password, clearPassword],
  );

  function save() {
    setNotice("");
    props
      .onSave({ ...draft, password: password.trim() || undefined, clear_password: clearPassword || undefined })
      .then(() => {
        setPassword("");
        setClearPassword(false);
        setNotice("Saved");
        api.panel().then(setPanel).catch(() => undefined);
        window.setTimeout(() => setNotice(""), 2400);
      })
      .catch((exc: Error) => setNotice(exc.message));
  }

  const choice = !draft.use_model ? "off" : draft.local_only ? "dev" : draft.profile;

  return (
    <div className={dirty ? "page settings dirty" : "page settings"}>
      <header className="page-head">
        <div>
          <h1>Settings</h1>
          <p className="lede">Everything stays on this computer. Keys go to Windows Credential Manager.</p>
        </div>
      </header>
      <nav className="tabs" aria-label="Settings sections">
        {TABS.map(([id, label]) => (
          <button key={id} type="button" className={tab === id ? "on" : undefined} aria-current={tab === id ? "page" : undefined} onClick={() => go(`#/settings/${id}`)}>
            {label}
          </button>
        ))}
      </nav>

      {tab === "general" ? (
        <>
          <Card title="New documents">
            <Toggle
              checked={draft.spark_content}
              onChange={(value) => setDraft({ ...draft, spark_content: value })}
              label="Include the company pages"
              hint="Client documents get Who We Are, Why Choose Us, and the contact pages. Internal documents leave them out. The composer can switch this for one message."
            />
          </Card>
          <Card title="Default model" note="The composer starts on this choice. You can pick another one for any message.">
            <div className="choice-grid">
              {(props.models?.options ?? []).map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={choice === item.id ? "choice on" : "choice"}
                  onClick={() =>
                    setDraft(item.id === "off" ? { ...draft, use_model: false } : { ...draft, use_model: true, profile: item.id })
                  }
                >
                  <span className="choice-label">
                    {item.label}
                    {choice === item.id ? <Check size={16} /> : null}
                  </span>
                  <span className="choice-detail">{item.available ? item.detail : item.reason}</span>
                </button>
              ))}
            </div>
          </Card>
          <Card title="Brand" note="Documents are set for this company. The brand pack is read only here.">
            <dl className="facts">
              <dt>Company</dt>
              <dd>{panel?.brand.name || "Spark Technologies"}</dd>
              <dt>Phone</dt>
              <dd>{panel?.brand.phone}</dd>
              <dt>Email</dt>
              <dd>{panel?.brand.email}</dd>
              {panel?.brand.website ? (
                <>
                  <dt>Website</dt>
                  <dd>{panel.brand.website}</dd>
                </>
              ) : null}
            </dl>
          </Card>
        </>
      ) : null}

      {tab === "models" ? (
        <>
          <Card title="Provider keys" note="A key is stored in Windows Credential Manager and is not shown again.">
            <Keys onChange={() => api.panel().then(setPanel).catch(() => undefined)} />
          </Card>
          <Card title="Local model" note="Ollama on this computer. It runs one job at a time.">
            <Field label="Address">
              <input value={draft.ollama_url} onChange={(event) => setDraft({ ...draft, ollama_url: event.target.value })} />
            </Field>
            <div className="field-row">
              <Field label="Context size">
                <select value={draft.num_ctx} onChange={(event) => setDraft({ ...draft, num_ctx: Number(event.target.value) })}>
                  {[4096, 8192, 16384, 32768].map((size) => (
                    <option key={size} value={size}>
                      {size.toLocaleString()} tokens
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Keep loaded for">
                <input value={draft.keep_alive} onChange={(event) => setDraft({ ...draft, keep_alive: event.target.value })} />
              </Field>
            </div>
            <p className="hint">32k is sent only when flash attention and 8 bit KV cache are on and the weights fit in video memory.</p>
          </Card>
          <Card title="Roles in this profile">
            {panel ? (
              <table className="table">
                <thead>
                  <tr>
                    <th>Role</th>
                    <th>Model</th>
                    <th className="num">Context</th>
                  </tr>
                </thead>
                <tbody>
                  {panel.catalog.map((item) => (
                    <tr key={item.role}>
                      <td>{item.role.replace("_", " ")}</td>
                      <td className="mono">{item.model}</td>
                      <td className="num">{item.context.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="hint">Loading</p>
            )}
            {panel?.lanes.length ? <p className="hint">{panel.lanes.join(". ")}.</p> : null}
          </Card>
        </>
      ) : null}

      {tab === "usage" ? <UsageTab usage={usage} panel={panel} draft={draft} setDraft={setDraft} /> : null}

      {tab === "privacy" ? (
        <>
          <Card title="Where work runs">
            <Toggle
              checked={draft.local_only}
              onChange={(value) => setDraft({ ...draft, local_only: value })}
              label="Keep everything on the local model"
              hint="Claude and OpenAI are not called while this is on, even when a key is stored."
            />
          </Card>
          <Card title="Logs">
            <div className="field-row">
              <Field label="Log detail">
                <select value={draft.log_level} onChange={(event) => setDraft({ ...draft, log_level: event.target.value as Settings["log_level"] })}>
                  <option value="metadata">Metadata only</option>
                  <option value="redacted">Redacted</option>
                  <option value="full">Full</option>
                </select>
              </Field>
              <Field label="Keep activity for (days)">
                <input type="number" min={1} max={3650} value={draft.retention_days} onChange={(event) => setDraft({ ...draft, retention_days: Number(event.target.value) })} />
              </Field>
            </div>
            <p className="hint">Older activity lines and run events are removed on save and when Docufy starts. Usage rows stay for the budget.</p>
          </Card>
          <Card title="App password" note="Optional. It is stored as a hash on this computer.">
            <Field label={props.settings.has_password ? "New password" : "Password"}>
              <input type="password" value={password} autoComplete="new-password" onChange={(event) => setPassword(event.target.value)} />
            </Field>
            {props.settings.has_password ? (
              <Toggle checked={clearPassword} onChange={setClearPassword} label="Remove the app password" />
            ) : null}
          </Card>
        </>
      ) : null}

      {tab === "data" ? <DataTab onImported={props.onImported} /> : null}
      {tab === "system" ? <SystemTab panel={panel} /> : null}
      {tab === "memory" ? <MemoryTab panel={panel} onChange={() => api.panel().then(setPanel).catch(() => undefined)} /> : null}

      {dirty || notice ? (
        <div className="save-bar" role="region" aria-label="Unsaved settings">
          <span>{notice || "You have unsaved changes"}</span>
          {dirty ? (
            <>
              <button
                type="button"
                className="ghost-button"
                onClick={() => {
                  setDraft(props.settings);
                  setPassword("");
                  setClearPassword(false);
                }}
              >
                Discard
              </button>
              <button type="button" className="primary-button" onClick={save}>
                Save changes
              </button>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function UsageTab({ usage, panel, draft, setDraft }: { usage: Usage | null; panel: Panel | null; draft: Settings; setDraft: (value: Settings) => void }) {
  const largest = Math.max(1, ...(usage?.models.map((item) => item.tokens) ?? [1]));
  const budget = usage?.budget;
  const ratio = Math.min(1, budget?.ratio ?? 0);
  return (
    <>
      <div className="stats">
        <Stat label="Today" value={usage?.windows.today.tokens ?? 0} cost={usage?.windows.today.cost_usd ?? 0} />
        <Stat label="Last 7 days" value={usage?.windows.week.tokens ?? 0} cost={usage?.windows.week.cost_usd ?? 0} />
        <Stat label="Last 30 days" value={usage?.windows.month.tokens ?? 0} cost={usage?.windows.month.cost_usd ?? 0} />
        <div className="stat">
          <span className="stat-label">Documents</span>
          <strong>{(usage?.documents ?? 0).toLocaleString()}</strong>
          <span className="stat-note">in the studio</span>
        </div>
      </div>
      <Card title="Budget">
        <div className="budget">
          <div className="meter large">
            <i className={budget?.level === "stop" ? "stop" : budget?.level === "warn" ? "warn" : undefined} style={{ width: `${Math.round(ratio * 100)}%` }} />
          </div>
          <p className="hint">
            ${(budget?.spent_usd ?? 0).toFixed(2)} of ${(budget?.monthly_usd ?? 0).toFixed(0)} used in the last 30 days.
            {budget?.level === "warn" ? " That is over 80 percent." : ""}
            {budget?.level === "stop" ? " The budget is spent." : ""}
          </p>
        </div>
        <div className="field-row">
          <Field label="Limit per document (USD)">
            <input type="number" min={0} step="0.5" value={draft.per_run_usd} onChange={(event) => setDraft({ ...draft, per_run_usd: Number(event.target.value) })} />
          </Field>
          <Field label="Monthly budget (USD)">
            <input type="number" min={0} step="1" value={draft.monthly_usd} onChange={(event) => setDraft({ ...draft, monthly_usd: Number(event.target.value) })} />
          </Field>
        </div>
        <Toggle checked={draft.hard_stop} onChange={(value) => setDraft({ ...draft, hard_stop: value })} label="Stop new model calls when the monthly budget is spent" />
      </Card>
      <Card title="Models this month" action={<a className="small-button" href="/api/usage.csv"><Download size={15} />Usage CSV</a>}>
        {usage && usage.models.length ? (
          <ul className="bars">
            {usage.models.map((item) => (
              <li key={item.model}>
                <span className="bar-name" title={item.model}>{item.label || item.model}</span>
                <span className="bar-track" aria-hidden="true">
                  <i style={{ width: `${Math.max(2, Math.round((item.tokens / largest) * 100))}%` }} />
                </span>
                <span className="bar-value">{item.tokens.toLocaleString()} tokens, ${item.cost_usd.toFixed(2)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="hint">No model calls yet.</p>
        )}
      </Card>
      <Card title="Recent runs">
        {panel && panel.runs.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Profile</th>
                <th>Status</th>
                <th className="num">Tokens</th>
                <th className="num">Cost</th>
              </tr>
            </thead>
            <tbody>
              {panel.runs.slice(0, 12).map((item) => (
                <tr key={item.id}>
                  <td>{item.source || item.kind}</td>
                  <td>{item.profile}</td>
                  <td>{item.status.replace(/_/g, " ")}</td>
                  <td className="num">{item.tokens.toLocaleString()}</td>
                  <td className="num">${item.cost_usd.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="hint">No runs yet.</p>
        )}
      </Card>
    </>
  );
}

function DataTab({ onImported }: { onImported: (chatId: string) => void }) {
  const [backups, setBackups] = useState<Backup[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.backups().then((data) => setBackups(data.backups)).catch(() => undefined);
  }, []);

  return (
    <>
      <Card
        title="Backups"
        note="A backup holds the database, chats, documents, and memory. One is taken when Docufy starts if the last is a day old. The last seven are kept."
        action={
          <button
            type="button"
            className="small-button primary"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              api
                .makeBackup()
                .then(() => api.backups())
                .then((data) => {
                  setBackups(data.backups);
                  setNote("Backup saved");
                })
                .catch((exc: Error) => setNote(exc.message))
                .finally(() => setBusy(false));
            }}
          >
            {busy ? "Backing up" : "Back up now"}
          </button>
        }
      >
        {backups.length ? (
          <ul className="list">
            {backups.map((item) => (
              <li key={item.name}>
                <span>
                  <strong>{when(item.created_at)}</strong>
                  <span className="hint"> {bytes(item.bytes)}</span>
                </span>
                <a className="small-button" href={api.backupUrl(item.name)}>
                  <Download size={15} />
                  Download
                </a>
              </li>
            ))}
          </ul>
        ) : (
          <p className="hint">No backups yet.</p>
        )}
        {note ? <p className="hint">{note}</p> : null}
        <p className="hint">To restore, close Docufy and run docufy restore with the backup file.</p>
      </Card>
      <Card title="Move a chat" note="Export a chat from its menu in the chat list. Import brings it back with its documents.">
        <input
          ref={input}
          type="file"
          accept=".zip"
          className="visually-hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (!file) return;
            setNote("");
            api
              .importChat(file)
              .then((chat) => onImported(chat.id))
              .catch((exc: Error) => setNote(exc.message));
          }}
        />
        <button type="button" className="small-button" onClick={() => input.current?.click()}>
          <Upload size={15} />
          Import a chat
        </button>
      </Card>
    </>
  );
}

function SystemTab({ panel }: { panel: Panel | null }) {
  const [checks, setChecks] = useState<ReadyCheck[] | null>(null);
  useEffect(() => {
    api.ready().then((data) => setChecks(data.checks)).catch(() => setChecks([]));
  }, []);
  return (
    <>
      <Card title="Setup check">
        {checks ? (
          <ul className="checks">
            {checks.map((item) => (
              <li key={item.name} className={item.ok ? "ok" : item.required ? "bad" : "warn"}>
                <span className="check-mark">{item.ok ? <Check size={15} /> : <Close size={15} />}</span>
                <span className="check-body">
                  <strong>{item.name}</strong>
                  <span className="hint">{item.detail}</span>
                  {!item.ok && item.fix ? <span className="check-fix">{item.fix}</span> : null}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="hint">Checking</p>
        )}
      </Card>
      <Card title="Health">
        <dl className="facts">
          <dt>Local model</dt>
          <dd>{panel?.health.ollama.detail || "checking"}</dd>
          <dt>Memory in use</dt>
          <dd>{Math.round(panel?.health.memory_percent || 0)}%</dd>
          <dt>Workspace</dt>
          <dd>{bytes(panel?.health.disk_bytes || 0)}</dd>
          <dt>Video memory free</dt>
          <dd>{panel?.health.vram_bytes ? bytes(panel.health.vram_bytes) : "Not reported on this machine"}</dd>
        </dl>
        {panel?.health.errors.length ? (
          <>
            <h3 className="sub">Recent model errors</h3>
            <ul className="list">
              {panel.health.errors.map((item) => (
                <li key={`${item.at}-${item.model}`}>
                  <span>{item.error}</span>
                  <span className="hint">{when(item.at)}</span>
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </Card>
      <Card title="Activity">
        {panel?.activity.length ? (
          <ul className="list">
            {panel.activity.map((item) => (
              <li key={item.id}>
                <span>
                  <strong>{item.kind}</strong> <span className="hint">{item.detail}</span>
                </span>
                <span className="hint">{when(item.at)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="hint">Nothing yet.</p>
        )}
      </Card>
    </>
  );
}

function MemoryTab({ panel, onChange }: { panel: Panel | null; onChange: () => void }) {
  const [note, setNote] = useState("");
  return (
    <Card title="What Docufy remembers" note="Preferences you gave and lessons from fixes that worked. A lesson from a hand edit waits for you to confirm it.">
      {panel && panel.memory.length ? (
        <ul className="list">
          {panel.memory.map((item) => (
            <li key={item.id}>
              <span>
                {item.text}
                <span className="hint"> {item.scope}</span>
              </span>
              {item.status === "candidate" ? (
                <button type="button" className="small-button" onClick={() => api.confirmMemory(item.id).then(onChange).catch((exc: Error) => setNote(exc.message))}>
                  Confirm
                </button>
              ) : (
                <span className="badge plain">{item.status}</span>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="hint">Nothing remembered yet.</p>
      )}
      {note ? <p className="hint">{note}</p> : null}
    </Card>
  );
}

function Keys({ onChange }: { onChange: () => void }) {
  const [rows, setRows] = useState<KeyStatus[]>([]);
  const [open, setOpen] = useState("");
  const [secret, setSecret] = useState("");
  const [note, setNote] = useState<{ name: string; text: string } | null>(null);

  useEffect(() => {
    api.keys().then((data) => setRows(data.keys)).catch(() => undefined);
  }, []);

  return (
    <ul className="keys">
      {rows.map((item) => (
        <li key={item.name}>
          <div className="key-row">
            <span className="key-name">{item.label}</span>
            <span className={item.set ? "badge" : "badge plain"}>{item.set ? "Stored" : "Not set"}</span>
            <span className="spacer" />
            {item.set ? (
              <button
                type="button"
                className="small-button"
                onClick={() => api.testKey(item.name).then((result) => setNote({ name: item.name, text: result.detail })).catch((exc: Error) => setNote({ name: item.name, text: exc.message }))}
              >
                Test
              </button>
            ) : null}
            <button
              type="button"
              className="small-button"
              onClick={() => {
                setOpen(open === item.name ? "" : item.name);
                setSecret("");
                setNote(null);
              }}
            >
              {item.set ? "Replace" : "Add key"}
            </button>
          </div>
          {open === item.name ? (
            <form
              className="key-form"
              onSubmit={(event) => {
                event.preventDefault();
                api
                  .saveKey(item.name, secret)
                  .then(() => api.keys())
                  .then((data) => {
                    setRows(data.keys);
                    setOpen("");
                    setSecret("");
                    setNote({ name: item.name, text: "Key stored" });
                    onChange();
                  })
                  .catch((exc: Error) => setNote({ name: item.name, text: exc.message }));
              }}
            >
              <input type="password" autoFocus value={secret} autoComplete="off" aria-label={`${item.label} key`} placeholder={`${item.label} key`} onChange={(event) => setSecret(event.target.value)} />
              <button type="submit" className="small-button primary" disabled={!secret.trim()}>
                Save
              </button>
            </form>
          ) : null}
          {note?.name === item.name ? <p className="hint">{note.text}</p> : null}
        </li>
      ))}
    </ul>
  );
}

function Card({ title, note, action, children }: { title: string; note?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <header className="card-head">
        <div>
          <h2>{title}</h2>
          {note ? <p className="hint">{note}</p> : null}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
    </label>
  );
}

function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (value: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="switch-row">
      <span className="switch-text">
        <span>{label}</span>
        {hint ? <span className="hint">{hint}</span> : null}
      </span>
      <input type="checkbox" role="switch" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span className="switch" aria-hidden="true" />
    </label>
  );
}

function Stat({ label, value, cost }: { label: string; value: number; cost: number }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <strong>{value.toLocaleString()}</strong>
      <span className="stat-note">tokens, ${cost.toFixed(2)}</span>
    </div>
  );
}
