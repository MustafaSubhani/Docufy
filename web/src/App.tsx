import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { api, ApiError, type ChatDetail, type ChatSummary, type DocumentInfo, type DraftInfo, type ModelChoice, type Models, type Settings, type SettingsDraft } from "./api";
import type { ComposerHandle } from "./components/Composer";
import { Files } from "./components/Files";
import { Lock } from "./components/Lock";
import { Palette } from "./components/Palette";
import { Workspace, type PanelTab } from "./components/Workspace";
import { Rail } from "./components/Rail";
import { SettingsPage } from "./components/Settings";
import { Thread } from "./components/Thread";
import { Close, Menu, Pages, PanelLeft, Plus } from "./icons";
import { chatIdFrom, go, settingsTab, useHash, useStored, viewFrom } from "./lib";

const RAIL = 264;
const RESEARCH_INTENT = /\b(research|look up|find out|tell me about|what are the common)\b/i;

export function App() {
  const hash = useHash();
  const view = viewFrom(hash);
  const chatId = chatIdFrom(hash);
  const [gate, setGate] = useState<"checking" | "locked" | "open">("checking");
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [chatQuery, setChatQuery] = useState("");
  const [chat, setChat] = useState<ChatDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<DocumentInfo | null>(null);
  const [editing, setEditing] = useState(true);
  const [panelOpen, setPanelOpen] = useState(() => window.innerWidth > 960);
  const [panelTab, setPanelTab] = useState<PanelTab>("pages");
  const [draftRoot, setDraftRoot] = useState("");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [models, setModels] = useState<Models | null>(null);
  const [model, setModel] = useStored<ModelChoice>("docufy.model", "off");
  const [railMode, setRailMode] = useStored<"shown" | "hidden">("docufy.rail", "shown");
  const [previewWidth, setPreviewWidth] = useStored<string>("docufy.preview", "");
  const [spark, setSpark] = useState(true);
  const [web, setWeb] = useState(() => window.localStorage.getItem("docufy.web") === "on");
  const [files, setFiles] = useState<DocumentInfo[]>([]);
  const [filesLoaded, setFilesLoaded] = useState(false);
  const [fileQuery, setFileQuery] = useState("");
  const [fileSpark, setFileSpark] = useState("");
  const [railOpen, setRailOpen] = useState(false);
  const [palette, setPalette] = useState(false);
  const [toast, setToast] = useState("");
  const known = useRef<{ chat: string; docs: Set<string>; drafts: Map<string, number> }>({ chat: "", docs: new Set(), drafts: new Map() });
  const wanted = useRef("");
  const modelSynced = useRef(false);
  const composer = useRef<ComposerHandle>(null);

  const fail = useCallback((exc: unknown, fallback: string) => {
    if (exc instanceof ApiError && exc.status === 401) {
      setGate("locked");
      return;
    }
    setToast(exc instanceof Error ? exc.message : fallback);
  }, []);

  const refreshChats = useCallback(
    (query = chatQuery) =>
      api
        .chats(query)
        .then((data) => setChats(data.chats))
        .catch((exc) => fail(exc, "Docufy is not running.")),
    [chatQuery, fail],
  );

  useEffect(() => {
    api.session().then((item) => setGate(item.required && !item.ok ? "locked" : "open")).catch(() => setGate("open"));
  }, []);

  useEffect(() => {
    if (gate !== "open") return;
    api
      .settings()
      .then((data) => {
        setSettings(data);
        setSpark(data.spark_content);
      })
      .catch((exc) => fail(exc, "Settings did not load."));
    api.models().then(setModels).catch(() => undefined);
    const timer = window.setInterval(() => api.models().then(setModels).catch(() => undefined), 30_000);
    return () => window.clearInterval(timer);
  }, [gate, fail]);

  useEffect(() => {
    if (!models || modelSynced.current) return;
    modelSynced.current = true;
    const stored = window.localStorage.getItem("docufy.model") as ModelChoice | null;
    const pick = stored ?? models.current;
    const option = models.options.find((item) => item.id === pick);
    setModel(option?.available ? pick : "off");
  }, [models, setModel]);

  useEffect(() => {
    if (gate !== "open") return;
    const timer = window.setTimeout(() => refreshChats(chatQuery), chatQuery ? 180 : 0);
    return () => window.clearTimeout(timer);
  }, [gate, chatQuery, refreshChats]);

  useEffect(() => {
    if (gate !== "open" || !chatId) {
      setChat(null);
      setPreview(null);
      setLoading(false);
      known.current = { chat: "", docs: new Set(), drafts: new Map() };
      return;
    }
    if (known.current.chat === chatId && chat?.id === chatId) return;
    let stop = false;
    setLoading(chat?.id !== chatId);
    api
      .chat(chatId)
      .then((detail) => {
        if (stop) return;
        known.current = { chat: detail.id, docs: new Set(detail.documents.map((item) => item.id)), drafts: draftMap(detail.drafts) };
        const pick = detail.documents.find((item) => item.id === wanted.current) ?? latest(detail.documents);
        const asked = Boolean(wanted.current);
        wanted.current = "";
        setChat(detail);
        setPreview(pick);
        setEditing(true);
        const lastDraft = detail.drafts.at(-1) ?? null;
        setDraftRoot(lastDraft?.root_id ?? "");
        const draftNewer = Boolean(lastDraft && (!pick || lastDraft.created_at > pick.created_at));
        setPanelTab(!asked && draftNewer ? "draft" : "pages");
        setPanelOpen(asked || window.innerWidth > 960);
      })
      .catch((exc) => {
        if (exc instanceof ApiError && exc.status === 404) {
          go("#/");
          return;
        }
        fail(exc, "The chat did not load.");
      })
      .finally(() => !stop && setLoading(false));
    return () => {
      stop = true;
    };
    // The chat object is read only to skip a reload after sending into a new chat.
  }, [chatId, gate, fail]);

  useEffect(() => {
    if (!chat?.busy) return;
    const timer = window.setInterval(() => {
      api
        .chat(chat.id)
        .then((detail) => {
          setChat(detail);
          const fresh = detail.documents.filter((item) => !known.current.docs.has(item.id));
          const grown = detail.drafts.filter((item) => (known.current.drafts.get(item.root_id) ?? 0) < item.version);
          known.current.drafts = draftMap(detail.drafts);
          if (grown.length) {
            setDraftRoot(grown[grown.length - 1].root_id);
            if (!fresh.length) setPanelTab("draft");
            setPanelOpen(true);
          }
          if (fresh.length) {
            fresh.forEach((item) => known.current.docs.add(item.id));
            setPreview(fresh[fresh.length - 1]);
            setEditing(true);
            setPanelTab("pages");
            setPanelOpen(true);
          }
          if (!detail.busy) refreshChats();
        })
        .catch(() => undefined);
    }, 700);
    return () => window.clearInterval(timer);
  }, [chat?.id, chat?.busy, refreshChats]);

  useEffect(() => {
    if (gate !== "open" || view !== "files") return;
    const timer = window.setTimeout(() => {
      api
        .documents(fileQuery, fileSpark)
        .then((data) => {
          setFiles(data.documents);
          setFilesLoaded(true);
        })
        .catch((exc) => fail(exc, "Files did not load."));
    }, 160);
    return () => window.clearTimeout(timer);
  }, [view, fileQuery, fileSpark, gate, fail]);

  useEffect(() => {
    document.title = view === "chat" && chat?.title && chat.title !== "New chat" ? `${chat.title}, Docufy` : "Docufy";
  }, [view, chat?.title]);

  useEffect(() => setRailOpen(false), [hash]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 6000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const typing = event.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette((value) => !value);
      } else if (event.key === "/" && !typing && view === "chat") {
        event.preventDefault();
        composer.current?.focus();
      } else if ((event.ctrlKey || event.metaKey) && event.key === "\\") {
        event.preventDefault();
        setRailMode(railMode === "hidden" ? "shown" : "hidden");
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view, railMode, setRailMode]);

  function navigate(next: string) {
    go(next);
    setRailOpen(false);
  }

  async function onSend(text: string, file: File | null, sectionIds?: string[], targetDoc?: string | null) {
    setToast("");
    const trimmed = text.trim();
    const useWeb = Boolean(web || (!file && model !== "off" && RESEARCH_INTENT.test(trimmed)));
    if (useWeb && !web && !file && model !== "off" && RESEARCH_INTENT.test(trimmed)) {
      setWeb(true);
      window.localStorage.setItem("docufy.web", "on");
      setToast("Web search was used for this research question.");
    }
    try {
      let id = chatId;
      if (!id) {
        const created = await api.createChat();
        id = created.id;
        known.current = { chat: id, docs: new Set(), drafts: new Map() };
      }
      const showingPages = panelOpen && panelTab === "pages";
      const target =
        targetDoc !== undefined ? targetDoc : editing && showingPages && preview && preview.chat_id === id ? preview.id : null;
      const detail = await api.send(id, text, {
        spark,
        web: useWeb,
        file,
        model,
        documentId: target,
        sectionIds: sectionIds?.length ? sectionIds : undefined,
        effort: model === "openai" ? window.localStorage.getItem("docufy.effort") || undefined : undefined,
        agentModel: model === "openai" ? window.localStorage.getItem("docufy.agentModel") || undefined : undefined,
      });
      setChat(detail);
      if (chatId !== id) navigate(`#/c/${id}`);
      refreshChats();
      return true;
    } catch (exc) {
      fail(exc, "The message was not sent.");
      return false;
    }
  }

  function onStop(messageId: string) {
    if (!chat) return;
    api.stop(chat.id, messageId).then(setChat).catch((exc) => fail(exc, "That did not stop."));
  }

  function onRetry(messageId: string, choice?: ModelChoice) {
    if (!chat) return;
    if (choice) setModel(choice);
    api.retry(chat.id, messageId, choice ?? model).then(setChat).catch((exc) => fail(exc, "That did not start again."));
  }

  async function onPublish(draftId: string, clientPages: boolean) {
    if (!chat) return;
    try {
      setChat(await api.publishDraft(draftId, clientPages, model));
      refreshChats();
    } catch (exc) {
      fail(exc, "The draft was not sent to the template.");
    }
  }

  function openDraft(draft: DraftInfo) {
    setDraftRoot(draft.root_id);
    setPanelTab("draft");
    setPanelOpen(true);
  }

  function openFile(doc: DocumentInfo) {
    if (doc.chat_id === chatId && chat) {
      setPreview(doc);
      setPanelTab("pages");
      setPanelOpen(true);
      navigate(`#/c/${doc.chat_id}`);
      return;
    }
    wanted.current = doc.id;
    navigate(`#/c/${doc.chat_id}`);
  }

  async function onSave(next: SettingsDraft) {
    const saved = await api.saveSettings(next);
    setSettings(saved);
    setSpark(saved.spark_content);
    const fresh = await api.models();
    setModels(fresh);
    const option = fresh.options.find((item) => item.id === fresh.current);
    if (option?.available) setModel(fresh.current);
  }

  function resizePreview(width: number) {
    if (!width) {
      setPreviewWidth("");
      return;
    }
    const rail = railMode === "hidden" ? 0 : RAIL;
    const clamped = Math.round(Math.max(380, Math.min(width, window.innerWidth - rail - 380)));
    setPreviewWidth(String(clamped));
  }

  if (gate !== "open") {
    return <Lock checking={gate === "checking"} onUnlock={() => setGate("open")} />;
  }

  const hasWork = Boolean(chat && (preview || chat.drafts.length));
  const showPreview = view === "chat" && chat !== null && panelOpen && hasWork;
  const hidden = railMode === "hidden";
  const shellClass = ["shell", showPreview ? "with-preview" : "", hidden ? "rail-hidden" : ""].filter(Boolean).join(" ");

  return (
    <div className={shellClass} style={previewWidth ? ({ ["--preview" as string]: `${previewWidth}px` } as CSSProperties) : undefined}>
      {railOpen ? <button className="scrim" type="button" aria-label="Close the menu" onClick={() => setRailOpen(false)} /> : null}
      <Rail
        open={railOpen}
        chats={chats}
        query={chatQuery}
        onQuery={setChatQuery}
        active={chatId}
        view={view}
        models={models}
        onNew={() => navigate("#/")}
        onOpen={(id) => navigate(`#/c/${id}`)}
        onNavigate={navigate}
        onHide={() => (railOpen ? setRailOpen(false) : setRailMode("hidden"))}
        onPalette={() => setPalette(true)}
        onRename={async (id, title) => {
          try {
            const detail = await api.renameChat(id, title);
            if (id === chatId) setChat(detail);
            refreshChats();
          } catch (exc) {
            fail(exc, "The chat was not renamed.");
          }
        }}
        onDelete={async (id) => {
          try {
            await api.removeChat(id);
            if (id === chatId) navigate("#/");
            refreshChats();
          } catch (exc) {
            fail(exc, "The chat was not deleted.");
          }
        }}
      />
      <main className="stage">
        <div className="topbar">
          <button type="button" className="icon-button" aria-label="Open the menu" onClick={() => setRailOpen(true)}>
            <Menu />
          </button>
          <strong>{view === "files" ? "Files" : view === "settings" ? "Settings" : chat?.title || "Docufy"}</strong>
          {view === "chat" && chat && !showPreview && hasWork ? (
            <button type="button" className="icon-button" aria-label="Open the workspace" onClick={() => setPanelOpen(true)}>
              <Pages />
            </button>
          ) : null}
        </div>
        {hidden ? (
          <div className="stage-tools">
            <button type="button" className="icon-button" aria-label="Show the sidebar" title="Show the sidebar" onClick={() => setRailMode("shown")}>
              <PanelLeft size={18} />
            </button>
            <button type="button" className="icon-button" aria-label="New chat" title="New chat" onClick={() => navigate("#/")}>
              <Plus size={18} />
            </button>
          </div>
        ) : null}
        {view === "chat" && chat && !showPreview && hasWork ? (
          <button type="button" className="reopen" onClick={() => setPanelOpen(true)}>
            <Pages size={16} />
            Show workspace
          </button>
        ) : null}
        {toast ? (
          <div className="toast" role="alert">
            <span>{toast}</span>
            <button type="button" className="icon-button" aria-label="Dismiss" onClick={() => setToast("")}>
              <Close size={16} />
            </button>
          </div>
        ) : null}
        <div className="view" key={view === "settings" ? "settings" : view}>
          {view === "files" ? (
            <Files query={fileQuery} onQuery={setFileQuery} spark={fileSpark} onSpark={setFileSpark} files={files} loaded={filesLoaded} onOpen={openFile} />
          ) : view === "settings" ? (
            settings ? (
              <SettingsPage
                tab={settingsTab(hash)}
                settings={settings}
                models={models}
                onSave={onSave}
                onImported={(id) => {
                  refreshChats();
                  navigate(`#/c/${id}`);
                }}
              />
            ) : null
          ) : (
            <Thread
              chat={chat}
              loading={loading}
              preview={preview}
              spark={spark}
              onSpark={setSpark}
              web={web}
              onWeb={(value) => {
                setWeb(value);
                window.localStorage.setItem("docufy.web", value ? "on" : "off");
              }}
              model={model}
              models={models}
              onModel={setModel}
              editing={editing}
              onEditing={setEditing}
              onSend={onSend}
              onStop={onStop}
              onRetry={onRetry}
              onOpen={(doc) => {
                setPreview(doc);
                setEditing(true);
                setPanelTab("pages");
                setPanelOpen(true);
              }}
              onOpenDraft={openDraft}
              openDraftId={showPreview && panelTab === "draft" ? draftRoot || chat?.drafts.at(-1)?.root_id || "" : ""}
              composerRef={composer}
            />
          )}
        </div>
      </main>
      {showPreview && chat ? (
        <Workspace
          chat={chat}
          tab={panelTab}
          onTab={setPanelTab}
          draftRoot={draftRoot}
          onDraftRoot={setDraftRoot}
          doc={preview}
          onPickDoc={(doc) => setPreview(chat.documents.find((item) => item.id === doc.id) ?? doc)}
          busy={Boolean(chat.busy)}
          spark={spark}
          onSend={(text, sections, targetDoc) => onSend(text, null, sections, targetDoc)}
          onPublish={onPublish}
          onChat={(detail) => {
            setChat(detail);
            refreshChats();
          }}
          onResize={resizePreview}
          onClose={() => setPanelOpen(false)}
        />
      ) : null}
      {palette ? (
        <Palette
          chats={chats}
          onClose={() => setPalette(false)}
          go={navigate}
          onNew={() => {
            navigate("#/");
            window.setTimeout(() => composer.current?.focus(), 50);
          }}
        />
      ) : null}
    </div>
  );
}

function draftMap(drafts: DraftInfo[]) {
  return new Map(drafts.map((item) => [item.root_id, item.version]));
}

function latest(documents: DocumentInfo[]) {
  return documents.length ? documents[documents.length - 1] : null;
}
