import { useEffect, useState, type ReactNode } from "react";
import { api, type McpServer, type Settings } from "../api";
import { Check, Close, Plus, Refresh, Spinner, Trash } from "../icons";

export function AgentCard({ draft, setDraft }: { draft: Settings; setDraft: (value: Settings) => void }) {
  const [models, setModels] = useState<string[]>([]);
  const [resolved, setResolved] = useState({ main: "", fast: "" });
  const [loading, setLoading] = useState(false);

  function load(refresh = false) {
    setLoading(true);
    api
      .openaiModels(refresh)
      .then((data) => {
        setModels(data.models);
        setResolved({ main: data.main, fast: data.fast });
      })
      .catch(() => setModels([]))
      .finally(() => setLoading(false));
  }

  useEffect(() => load(false), []);

  return (
    <section className="card">
      <header className="card-head">
        <div>
          <h2>Agent</h2>
          <p className="hint">
            With OpenAI selected, every message goes to one agent that searches the web, writes drafts in the canvas, sets them in the template and edits the pages, using tools as it needs them.
          </p>
        </div>
        <button type="button" className="small-button" onClick={() => load(true)} disabled={loading}>
          {loading ? <Spinner size={13} /> : <Refresh size={14} />}
          Models
        </button>
      </header>
      <label className="switch-row">
        <span className="switch-text">
          <span>Use the agent</span>
          <span className="hint">Off keeps the classic pipeline: one fixed step per message.</span>
        </span>
        <input type="checkbox" role="switch" checked={draft.agent} onChange={(event) => setDraft({ ...draft, agent: event.target.checked })} />
        <span className="switch" aria-hidden="true" />
      </label>
      <div className="field-row">
        <Field label="Main model">
          <ModelSelect value={draft.openai_model} models={models} resolved={resolved.main} onChange={(value) => setDraft({ ...draft, openai_model: value })} />
        </Field>
        <Field label="Fast model (summaries)">
          <ModelSelect value={draft.openai_fast_model} models={models} resolved={resolved.fast} onChange={(value) => setDraft({ ...draft, openai_fast_model: value })} />
        </Field>
      </div>
      <div className="field-row">
        <Field label="Reasoning effort">
          <select value={draft.reasoning_effort} onChange={(event) => setDraft({ ...draft, reasoning_effort: event.target.value as Settings["reasoning_effort"] })}>
            <option value="minimal">Minimal, fastest</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High, most careful</option>
          </select>
        </Field>
        <Field label="Web search depth">
          <select value={draft.search_context} onChange={(event) => setDraft({ ...draft, search_context: event.target.value as Settings["search_context"] })}>
            <option value="low">Low, cheapest</option>
            <option value="medium">Medium</option>
            <option value="high">High, most context</option>
          </select>
        </Field>
      </div>
      <label className="switch-row">
        <span className="switch-text">
          <span>OpenAI web search</span>
          <span className="hint">Off uses Tavily, Serper or Brave when a key is stored, then DuckDuckGo.</span>
        </span>
        <input type="checkbox" role="switch" checked={draft.hosted_search} onChange={(event) => setDraft({ ...draft, hosted_search: event.target.checked })} />
        <span className="switch" aria-hidden="true" />
      </label>
      <label className="switch-row">
        <span className="switch-text">
          <span>Agent on the local model</span>
          <span className="hint">Experimental. Small local models call tools poorly; the classic pipeline is steadier on them.</span>
        </span>
        <input type="checkbox" role="switch" checked={draft.agent_local} onChange={(event) => setDraft({ ...draft, agent_local: event.target.checked })} />
        <span className="switch" aria-hidden="true" />
      </label>
    </section>
  );
}

function ModelSelect({ value, models, resolved, onChange }: { value: string; models: string[]; resolved: string; onChange: (value: string) => void }) {
  const known = !value || models.includes(value);
  return (
    <select value={known ? value : "__custom"} onChange={(event) => onChange(event.target.value === "__custom" ? value : event.target.value)}>
      <option value="">Newest available{resolved ? ` (${resolved})` : ""}</option>
      {models.map((name) => (
        <option key={name} value={name}>
          {name}
        </option>
      ))}
      {!known ? <option value="__custom">{value} (not on this key)</option> : null}
    </select>
  );
}

const EMPTY: McpServer = { name: "", transport: "stdio", enabled: true, command: "", args: [], env: {}, url: "", headers: {} };

export function McpTab() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [edit, setEdit] = useState<McpServer | null>(null);
  const [index, setIndex] = useState(-1);
  const [note, setNote] = useState("");
  const [testing, setTesting] = useState("");

  useEffect(() => {
    api.mcp().then((data) => setServers(data.servers)).catch((exc: Error) => setNote(exc.message));
  }, []);

  function save(next: McpServer[]) {
    setNote("");
    return api
      .saveMcp(next.map(strip))
      .then((data) => {
        setServers(data.servers);
        setEdit(null);
        setIndex(-1);
      })
      .catch((exc: Error) => setNote(exc.message));
  }

  function test(name: string) {
    setTesting(name);
    setNote("");
    api
      .testMcp(name)
      .then((result) => {
        setNote(result.ok ? `${name}: connected, ${result.tools.length} tools` : `${name}: ${result.error}`);
        return api.mcp();
      })
      .then((data) => setServers(data.servers))
      .catch((exc: Error) => setNote(exc.message))
      .finally(() => setTesting(""));
  }

  return (
    <section className="card">
      <header className="card-head">
        <div>
          <h2>MCP servers</h2>
          <p className="hint">
            Tools from these servers are offered to the agent as mcp__server__tool. A stdio server starts once and stays connected. Remote servers use Streamable HTTP or SSE.
          </p>
        </div>
        <button
          type="button"
          className="small-button primary"
          onClick={() => {
            setEdit({ ...EMPTY });
            setIndex(-1);
          }}
        >
          <Plus size={14} />
          Add server
        </button>
      </header>
      {servers.length ? (
        <ul className="mcp-list">
          {servers.map((item, i) => (
            <li key={item.name} className="mcp-row">
              <span className={`mcp-dot ${item.status || "idle"}`} title={item.status} />
              <span className="mcp-main">
                <strong>{item.name}</strong>
                <span className="hint mono">{item.transport === "stdio" ? [item.command, ...(item.args || [])].join(" ") : item.url}</span>
                {item.error ? <span className="mcp-error">{item.error}</span> : null}
                {item.tools?.length ? (
                  <span className="mcp-tools">
                    {item.tools.slice(0, 12).map((tool) => (
                      <span key={tool.name} className="mcp-tool" title={tool.description}>
                        {tool.name}
                      </span>
                    ))}
                    {item.tools.length > 12 ? <span className="mcp-tool">+{item.tools.length - 12}</span> : null}
                  </span>
                ) : null}
              </span>
              <button type="button" className="small-button" onClick={() => test(item.name)} disabled={testing === item.name}>
                {testing === item.name ? <Spinner size={13} /> : <Check size={14} />}
                Test
              </button>
              <button
                type="button"
                className="small-button"
                onClick={() => {
                  setEdit({ ...item });
                  setIndex(i);
                }}
              >
                Edit
              </button>
              <button type="button" className="icon-button" aria-label={`Remove ${item.name}`} onClick={() => save(servers.filter((_, j) => j !== i))}>
                <Trash size={16} />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="hint">No servers yet. For example, a filesystem server: command npx, arguments -y @modelcontextprotocol/server-filesystem C:\path\to\folder.</p>
      )}
      {edit ? (
        <McpForm
          value={edit}
          onCancel={() => setEdit(null)}
          onSave={(next) => save(index >= 0 ? servers.map((item, i) => (i === index ? next : item)) : [...servers, next])}
        />
      ) : null}
      {note ? <p className="hint">{note}</p> : null}
    </section>
  );
}

function McpForm({ value, onCancel, onSave }: { value: McpServer; onCancel: () => void; onSave: (next: McpServer) => void }) {
  const [item, setItem] = useState<McpServer>(value);
  const [args, setArgs] = useState((value.args || []).join("\n"));
  const [env, setEnv] = useState(pairs(value.env));
  const [headers, setHeaders] = useState(pairs(value.headers));
  return (
    <form
      className="mcp-form"
      onSubmit={(event) => {
        event.preventDefault();
        onSave({
          ...item,
          args: args.split("\n").map((line) => line.trim()).filter(Boolean),
          env: unpairs(env),
          headers: unpairs(headers),
        });
      }}
    >
      <div className="field-row">
        <Field label="Name">
          <input value={item.name} placeholder="files" onChange={(event) => setItem({ ...item, name: event.target.value })} />
        </Field>
        <Field label="Transport">
          <select value={item.transport} onChange={(event) => setItem({ ...item, transport: event.target.value as McpServer["transport"] })}>
            <option value="stdio">stdio (a local command)</option>
            <option value="http">Streamable HTTP</option>
            <option value="sse">SSE</option>
          </select>
        </Field>
      </div>
      {item.transport === "stdio" ? (
        <>
          <Field label="Command">
            <input value={item.command || ""} placeholder="npx" onChange={(event) => setItem({ ...item, command: event.target.value })} />
          </Field>
          <Field label="Arguments, one per line">
            <textarea rows={3} value={args} placeholder={"-y\n@modelcontextprotocol/server-filesystem\nC:\\Docs"} onChange={(event) => setArgs(event.target.value)} />
          </Field>
          <Field label="Environment, KEY=value per line">
            <textarea rows={2} value={env} onChange={(event) => setEnv(event.target.value)} />
          </Field>
        </>
      ) : (
        <>
          <Field label="Address">
            <input value={item.url || ""} placeholder="https://example.com/mcp" onChange={(event) => setItem({ ...item, url: event.target.value })} />
          </Field>
          <Field label="Headers, Name=value per line">
            <textarea rows={2} value={headers} placeholder="Authorization=Bearer ..." onChange={(event) => setHeaders(event.target.value)} />
          </Field>
        </>
      )}
      <label className="switch-row">
        <span className="switch-text">
          <span>Enabled</span>
        </span>
        <input type="checkbox" role="switch" checked={item.enabled} onChange={(event) => setItem({ ...item, enabled: event.target.checked })} />
        <span className="switch" aria-hidden="true" />
      </label>
      <div className="source-actions">
        <button type="button" className="small-button" onClick={onCancel}>
          <Close size={14} />
          Cancel
        </button>
        <button type="submit" className="small-button primary" disabled={!item.name.trim()}>
          Save server
        </button>
      </div>
    </form>
  );
}

function strip(item: McpServer): McpServer {
  const { status: _status, error: _error, tools: _tools, ...rest } = item;
  return rest;
}

function pairs(map?: Record<string, string>) {
  return Object.entries(map || {})
    .map(([key, val]) => `${key}=${val}`)
    .join("\n");
}

function unpairs(text: string) {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const at = line.indexOf("=");
    if (at > 0) out[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return out;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
    </label>
  );
}
