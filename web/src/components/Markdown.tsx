import { Renderer } from "@openuidev/react-lang";
import { memo, useEffect, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api";
import { Check, Copy } from "../icons";
import { chatLibrary } from "../openui/chat";

const svgCache = new Map<string, string | null>();

/** Markdown for replies and drafts. Mermaid is drawn by the template's diagram engine, openui-lang by OpenUI. */
export const Markdown = memo(function Markdown({ text, streaming = false, variant = "chat" }: { text: string; streaming?: boolean; variant?: "chat" | "doc" }) {
  const components: Components = {
    a: ({ href, children }) => (
      <a href={href} target="_blank" rel="noreferrer">
        {children}
      </a>
    ),
    table: ({ children }) => (
      <div className="md-table">
        <table>{children}</table>
      </div>
    ),
    pre: ({ children }) => <>{children}</>,
    code: ({ className, children }) => {
      const lang = /language-([\w-]+)/.exec(className || "")?.[1]?.toLowerCase() ?? "";
      const source = String(children ?? "").replace(/\n$/, "");
      if (!className && !source.includes("\n")) return <code>{children}</code>;
      if (lang === "mermaid" || lang === "diagram") return <Diagram source={source} streaming={streaming} />;
      if (lang === "openui-lang" || lang === "openui") {
        if (variant === "doc") return <CodeBlock lang={lang} source={source} />;
        return (
          <div className="oui-inline">
            <Renderer response={source} library={chatLibrary} isStreaming={streaming} onError={() => undefined} />
          </div>
        );
      }
      return <CodeBlock lang={lang} source={source} />;
    },
    blockquote: ({ children }) => <blockquote className="md-quote">{children}</blockquote>,
  };
  return (
    <div className={variant === "doc" ? "md md-doc" : "md"}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
});

function CodeBlock({ lang, source }: { lang: string; source: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="md-code">
      <div className="md-code-head">
        <span>{lang || "text"}</span>
        <button
          type="button"
          className="md-code-copy"
          onClick={() =>
            navigator.clipboard?.writeText(source).then(() => {
              setDone(true);
              window.setTimeout(() => setDone(false), 1200);
            })
          }
        >
          {done ? <Check size={13} /> : <Copy size={13} />}
          {done ? "Copied" : "Copy"}
        </button>
      </div>
      <pre>
        <code>{source}</code>
      </pre>
    </div>
  );
}

export function Diagram({ source, streaming }: { source: string; streaming?: boolean }) {
  const [svg, setSvg] = useState<string | null | undefined>(() => svgCache.get(source));
  const [showSource, setShowSource] = useState(false);

  useEffect(() => {
    if (svgCache.has(source)) {
      setSvg(svgCache.get(source));
      return;
    }
    let stop = false;
    const timer = window.setTimeout(
      () => {
        api
          .diagram(source)
          .then((text) => {
            svgCache.set(source, text);
            if (!stop) setSvg(text);
          })
          .catch(() => {
            if (!streaming) svgCache.set(source, null);
            if (!stop) setSvg(null);
          });
      },
      streaming ? 900 : 60,
    );
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [source, streaming]);

  let body: ReactNode;
  if (svg) body = <div className="md-diagram-svg" dangerouslySetInnerHTML={{ __html: svg }} />;
  else if (svg === null && !streaming) body = <p className="md-diagram-note">This diagram does not parse yet. It is shown as source.</p>;
  else body = <div className="md-diagram-wait" aria-label="Drawing the diagram" />;

  return (
    <figure className="md-diagram">
      {body}
      <figcaption>
        <span>Diagram, drawn in the template style</span>
        <button type="button" className="link-button" onClick={() => setShowSource((value) => !value)}>
          {showSource ? "Hide source" : "Source"}
        </button>
      </figcaption>
      {showSource || (svg === null && !streaming) ? (
        <pre className="md-diagram-src">
          <code>{source}</code>
        </pre>
      ) : null}
    </figure>
  );
}
