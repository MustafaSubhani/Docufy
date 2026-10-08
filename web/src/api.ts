export type ChatSummary = {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  busy?: boolean;
  documents?: number;
};

export type DocumentInfo = {
  id: string;
  chat_id: string;
  title: string;
  pages: number;
  spark_content: boolean;
  created_at: number;
  version?: number;
  parent_id?: string;
  root_id?: string;
  snippet?: string;
  chat_title?: string;
};

export type DocumentSection = {
  id: string;
  label: string;
  kicker: string;
  title: string;
  pages: number[];
};

export type DocumentDetail = DocumentInfo & {
  versions: { id: string; version: number; created_at: number; pages: number }[];
  sections?: DocumentSection[];
};

export type Progress = { text: string; streaming?: boolean };

export type Step = {
  key: string;
  label: string;
  kind: "step" | "agent" | "item" | "tool" | "thought";
  parent: string;
  status: "running" | "done" | "failed" | "stopped";
  detail: string;
  started: number;
  ended: number | null;
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  body: string;
  status: "ready" | "writing" | "failed" | "stopped" | "blocked";
  activity?: Step[];
  document_id: string | null;
  created_at: number;
  model?: string;
  model_label?: string;
  progress?: Progress | null;
  attachment?: string;
  target_id?: string;
  web?: boolean;
  sources?: Source[];
  usage?: TurnUsage | null;
  drafts?: string[];
  sections?: string[];
};

export type Source = { url: string; title: string };

export type TurnUsage = {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  searches: number;
  rounds: number;
  model: string;
};

export type DraftInfo = {
  id: string;
  root_id: string;
  version: number;
  title: string;
  words: number;
  document_root: string;
  created_at: number;
  author: string;
  note: string;
};

export type DraftDetail = DraftInfo & {
  body: string;
  outline: string[];
  chat_id: string;
  latest_id: string;
  versions: { id: string; version: number; created_at: number; note: string; author: string }[];
};

export type ChatNote = { id: string; text: string; created_at: number };

export type McpServer = {
  name: string;
  transport: "stdio" | "http" | "sse";
  enabled: boolean;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headers?: Record<string, string>;
  allow?: string[];
  description?: string;
  status?: "idle" | "connecting" | "ready" | "failed" | "off";
  error?: string;
  tools?: { name: string; description: string }[];
};

export type ChatDetail = ChatSummary & {
  messages: Message[];
  documents: DocumentInfo[];
  drafts: DraftInfo[];
  notes: ChatNote[];
};

export type ModelChoice = "off" | "dev" | "claude" | "openai";

export type ModelOption = {
  id: ModelChoice;
  label: string;
  detail: string;
  model: string;
  available: boolean;
  reason: string;
};

export type Models = { options: ModelOption[]; current: ModelChoice };

export type Settings = {
  profile: "dev" | "claude" | "openai";
  use_model: boolean;
  spark_content: boolean;
  per_run_usd: number;
  monthly_usd: number;
  hard_stop: boolean;
  log_level: "full" | "redacted" | "metadata";
  retention_days: number;
  local_only: boolean;
  num_ctx: number;
  keep_alive: string;
  ollama_url: string;
  agent: boolean;
  agent_local: boolean;
  openai_model: string;
  openai_fast_model: string;
  reasoning_effort: "minimal" | "low" | "medium" | "high";
  search_context: "low" | "medium" | "high";
  hosted_search: boolean;
  has_password?: boolean;
};

export type SettingsDraft = Settings & { password?: string; clear_password?: boolean };

export type Usage = {
  windows: Record<string, { calls: number; tokens: number; cost_usd: number }>;
  models: { model: string; label?: string; calls: number; tokens: number; cost_usd: number }[];
  providers: { provider: string; calls: number; tokens: number; cost_usd: number }[];
  documents: number;
  budget: { monthly_usd: number; spent_usd: number; ratio: number; level: "ok" | "warn" | "stop" };
};

export type KeyStatus = { name: string; label: string; set: boolean };

export type Panel = {
  keys: KeyStatus[];
  runs: { id: string; kind: string; profile: string; status: string; started_at: number; source: string; tokens: number; cost_usd: number }[];
  tools: { id: number; name: string; cost_usd: number; ok: boolean; detail: string; at: number }[];
  health: {
    ollama: { ok: boolean; detail: string };
    vram_bytes: number | null;
    disk_bytes: number;
    memory_percent: number;
    errors: { at: number; model: string; error: string }[];
  };
  catalog: { role: string; model: string; context: number; max_output: number; vision: boolean }[];
  lanes: string[];
  memory: { id: string; status: string; scope: string; text: string }[];
  brand: { name: string; phone: string; email: string; website: string };
  activity: { id: number; at: number; kind: string; detail: string }[];
};

export type Backup = { name: string; bytes: number; created_at: number };
export type ReadyCheck = { name: string; ok: boolean; required: boolean; detail: string; fix: string };

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function read<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      /* the body was not JSON */
    }
    throw new ApiError(typeof detail === "string" ? sentence(detail) : "Docufy could not complete that.", response.status);
  }
  return response.json() as Promise<T>;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

function sentence(text: string) {
  const clean = text.trim();
  if (!clean) return "Docufy could not complete that.";
  const first = clean[0].toUpperCase() + clean.slice(1);
  return /[.!?]$/.test(first) ? first : `${first}.`;
}

export type SendOptions = {
  spark: boolean;
  web?: boolean;
  file?: File | null;
  model: ModelChoice;
  documentId?: string | null;
  sectionIds?: string[];
  effort?: string;
  agentModel?: string;
};

export const api = {
  session: () => read<{ required: boolean; ok: boolean }>("/api/session"),
  login: (password: string) => read<{ ok: boolean }>("/api/login", json("POST", { password })),
  chats: (q = "") => read<{ chats: ChatSummary[] }>(`/api/chats?q=${encodeURIComponent(q)}`),
  createChat: () => read<ChatSummary>("/api/chats", { method: "POST" }),
  chat: (id: string) => read<ChatDetail>(`/api/chats/${id}`),
  renameChat: (id: string, title: string) => read<ChatDetail>(`/api/chats/${id}`, json("PATCH", { title })),
  removeChat: (id: string) => read<{ ok: boolean }>(`/api/chats/${id}`, { method: "DELETE" }),
  exportUrl: (id: string) => `/api/chats/${id}/export`,
  importChat: (file: File) => {
    const form = new FormData();
    form.set("file", file);
    return read<ChatDetail>("/api/import", { method: "POST", body: form });
  },
  send: (id: string, text: string, options: SendOptions) => {
    if (options.file) {
      const form = new FormData();
      form.set("text", text);
      form.set("spark_content", options.spark ? "true" : "false");
      form.set("web", options.web ? "true" : "false");
      form.set("model", options.model);
      if (options.documentId) form.set("document_id", options.documentId);
      if (options.sectionIds?.length) form.set("section_ids", JSON.stringify(options.sectionIds));
      if (options.effort) form.set("effort", options.effort);
      if (options.agentModel) form.set("agent_model", options.agentModel);
      form.set("file", options.file);
      return read<ChatDetail>(`/api/chats/${id}/messages`, { method: "POST", body: form });
    }
    return read<ChatDetail>(
      `/api/chats/${id}/messages`,
      json("POST", {
        text,
        spark_content: options.spark,
        web: Boolean(options.web),
        model: options.model,
        document_id: options.documentId || null,
        section_ids: options.sectionIds?.length ? options.sectionIds : null,
        effort: options.effort || null,
        agent_model: options.agentModel || null,
      }),
    );
  },
  stop: (chatId: string, messageId: string) => read<ChatDetail>(`/api/chats/${chatId}/messages/${messageId}/stop`, { method: "POST" }),
  retry: (chatId: string, messageId: string, model: ModelChoice) =>
    read<ChatDetail>(`/api/chats/${chatId}/messages/${messageId}/retry`, json("POST", { model })),
  models: () => read<Models>("/api/models"),
  documents: (q = "", spark = "") =>
    read<{ documents: DocumentInfo[] }>(`/api/documents?q=${encodeURIComponent(q)}&spark=${encodeURIComponent(spark)}`),
  document: (id: string) => read<DocumentDetail>(`/api/documents/${id}`),
  pageUrl: (id: string, page: number, doc?: { version?: number; pages: number }) => {
    const stamp = doc ? `${doc.version || 1}-${doc.pages}` : "";
    return `/api/documents/${id}/pages/${page}.png${stamp ? `?v=${stamp}` : ""}`;
  },
  downloadUrl: (id: string, kind: "pdf" | "docx" | "md") => `/api/documents/${id}/download?kind=${kind}`,
  settings: () => read<Settings>("/api/settings"),
  saveSettings: (body: SettingsDraft) => read<Settings>("/api/settings", json("PUT", body)),
  usage: () => read<Usage>("/api/usage"),
  panel: () => read<Panel>("/api/panel"),
  keys: () => read<{ keys: KeyStatus[] }>("/api/settings/keys"),
  saveKey: (name: string, secret: string) => read<KeyStatus>("/api/settings/keys", json("PUT", { name, secret })),
  testKey: (name: string) => read<{ ok: boolean; detail: string }>("/api/settings/keys/test", json("POST", { name })),
  confirmMemory: (id: string) => read<{ ok: boolean }>(`/api/memory/${id}/confirm`, { method: "POST" }),
  backups: () => read<{ backups: Backup[] }>("/api/backups"),
  makeBackup: () => read<{ name: string; bytes: number }>("/api/backups", { method: "POST" }),
  backupUrl: (name: string) => `/api/backups/${encodeURIComponent(name)}`,
  ready: () => read<{ ok: boolean; checks: ReadyCheck[] }>("/api/ready"),
  draft: (id: string) => read<DraftDetail>(`/api/drafts/${id}`),
  saveDraft: (id: string, markdown: string, title?: string) => read<DraftDetail>(`/api/drafts/${id}`, json("PUT", { markdown, title })),
  publishDraft: (id: string, clientPages: boolean, model: ModelChoice) =>
    read<ChatDetail>(`/api/drafts/${id}/publish`, json("POST", { client_pages: clientPages, model })),
  diagram: async (source: string) => {
    const response = await fetch("/api/diagram", json("POST", { source }));
    if (!response.ok) throw new ApiError("diagram", response.status);
    return response.text();
  },
  documentSource: (id: string) => read<{ id: string; markdown: string }>(`/api/documents/${id}/source`),
  saveDocumentSource: (id: string, markdown: string) => read<ChatDetail>(`/api/documents/${id}/source`, json("PUT", { markdown })),
  restoreVersion: (id: string) => read<ChatDetail>(`/api/documents/${id}/restore`, { method: "POST" }),
  dropNote: (chatId: string, noteId: string) => read<{ ok: boolean }>(`/api/chats/${chatId}/notes/${noteId}`, { method: "DELETE" }),
  mcp: () => read<{ servers: McpServer[] }>("/api/mcp"),
  saveMcp: (servers: McpServer[]) => read<{ servers: McpServer[] }>("/api/mcp", json("PUT", { servers })),
  testMcp: (name: string) => read<{ ok: boolean; error: string; tools: string[] }>(`/api/mcp/${encodeURIComponent(name)}/test`, { method: "POST" }),
  openaiModels: (refresh = false) => read<{ models: string[]; main: string; fast: string }>(`/api/openai/models${refresh ? "?refresh=true" : ""}`),
};
