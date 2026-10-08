import { createParser } from "@openuidev/lang-core";
import { Renderer } from "@openuidev/react-lang";
import { useMemo } from "react";
import { Rich } from "../lib";
import { extractOpenUILang, isResearchBody, markdownFallback } from "../openui/extract";
import { researchLibrary } from "../openui/library";
import { parseResearchView } from "../openui/parseLoose";
import { ResearchViewPanel } from "../openui/ResearchViewPanel";

const schemaParser = createParser(researchLibrary.toJSONSchema());

export function ResearchReply({ text, streaming }: { text: string; streaming?: boolean }) {
  const lang = extractOpenUILang(text);
  const fallback = markdownFallback(text);
  const parsed = useMemo(() => (lang ? parseResearchView(lang) : null), [lang]);
  const rendererOk = useMemo(() => {
    if (!lang) return false;
    try {
      const result = schemaParser.parse(lang);
      return Boolean(result.root);
    } catch {
      return false;
    }
  }, [lang]);

  if (lang && parsed) {
    return (
      <div className="oui-wrap">
        {rendererOk ? (
          <Renderer response={lang} library={researchLibrary} isStreaming={Boolean(streaming)} />
        ) : (
          <ResearchViewPanel {...parsed} />
        )}
        {!streaming && fallback ? (
          <div className="oui-fallback">
            <Rich text={fallback} />
          </div>
        ) : null}
      </div>
    );
  }

  if (isResearchBody(text)) {
    return <p className="oui-prose hint">The research layout could not be read. Try again or switch to Claude or OpenAI.</p>;
  }

  return <Rich text={text} />;
}
