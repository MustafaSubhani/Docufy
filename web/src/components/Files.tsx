import { api, type DocumentInfo } from "../api";
import { Search } from "../icons";
import { pages, when } from "../lib";

const FILTERS = [
  ["", "All"],
  ["on", "Client pages"],
  ["off", "Internal"],
] as const;

export function Files({
  query,
  onQuery,
  spark,
  onSpark,
  files,
  loaded,
  onOpen,
}: {
  query: string;
  onQuery: (value: string) => void;
  spark: string;
  onSpark: (value: string) => void;
  files: DocumentInfo[];
  loaded: boolean;
  onOpen: (doc: DocumentInfo) => void;
}) {
  return (
    <div className="page files">
      <header className="page-head">
        <div>
          <h1>Files</h1>
          <p className="lede">{loaded ? (files.length === 1 ? "1 document" : `${files.length} documents`) : "Loading"}</p>
        </div>
        <div className="files-tools">
          <label className="search wide">
            <Search size={16} />
            <span className="visually-hidden">Search files</span>
            <input value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Search titles, notes, and chats" />
          </label>
          <div className="toggle" role="radiogroup" aria-label="Company pages">
            {FILTERS.map(([id, label]) => (
              <button key={id || "all"} type="button" role="radio" aria-checked={spark === id} className={spark === id ? "on" : undefined} onClick={() => onSpark(id)}>
                {label}
              </button>
            ))}
          </div>
        </div>
      </header>
      {loaded && files.length === 0 ? (
        <div className="empty-card">
          <h2>{query || spark ? "Nothing matches that search" : "No documents yet"}</h2>
          <p>{query || spark ? "Try a word from the notes or the chat name." : "Write one in a chat and it will be listed here with its pages."}</p>
        </div>
      ) : (
        <ul className="file-grid">
          {files.map((doc) => (
            <li key={doc.id}>
              <button type="button" className="file-card" onClick={() => onOpen(doc)}>
                <span className="file-thumb">
                  <img src={api.pageUrl(doc.id, 1, doc)} alt="" loading="lazy" />
                </span>
                <span className="file-info">
                  <span className="file-title">{doc.title}</span>
                  <span className="file-meta">
                    {when(doc.created_at)}, {pages(doc.pages)}
                  </span>
                  <span className="file-tags">
                    <span className={doc.spark_content ? "badge" : "badge plain"}>{doc.spark_content ? "Client pages" : "Internal"}</span>
                    {doc.version && doc.version > 1 ? <span className="badge plain">Version {doc.version}</span> : null}
                  </span>
                  {doc.snippet ? <span className="file-snippet">{doc.snippet}</span> : null}
                  <span className="file-chat">From {doc.chat_title}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
