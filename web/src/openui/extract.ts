/** Fix `{ problem = "x" }` objects local models often emit instead of colons. */
export function normalizeOpenUILang(lang: string): string {
  return lang.replace(/\{([^{}]*)\}/g, (_, inner: string) => `{${inner.replace(/(\b[a-zA-Z_][\w]*)\s*=/g, "$1:")}}`);
}

/** ResearchView(ask: "...", ...) -> positional args for the parser. */
export function convertNamedResearchView(lang: string): string {
  if (!/ResearchView\s*\(\s*(?:ask|context|insights|gaps|sources)\s*:/i.test(lang)) {
    return lang;
  }
  const call = lang.match(/ResearchView\s*\(\s*([\s\S]*?)\s*\)/);
  if (!call?.[1]) return lang;
  const inner = call[1];
  const read = (key: string) => {
    const re = new RegExp(
      `${key}\\s*:\\s*([\\s\\S]*?)(?=\\n\\s*(?:ask|context|insights|gaps|sources)\\s*:|$)`,
      "i",
    );
    const found = inner.match(re);
    return found?.[1]?.trim().replace(/,\s*$/, "") ?? "";
  };
  const ask = read("ask");
  const context = read("context");
  const insights = read("insights");
  const gaps = read("gaps");
  const sources = read("sources");
  if (!ask || !context || !insights || !gaps || !sources) return lang;
  const positional = `ResearchView(${ask}, ${context}, ${insights}, ${gaps}, ${sources})`;
  return lang.replace(/ResearchView\s*\(\s*[\s\S]*?\s*\)/, positional);
}

function unwrapResearchText(raw: string): string {
  const fence = raw.match(/```(?:openui|openui-lang)?\s*\n([\s\S]*?)```/i);
  if (fence?.[1]) return fence[1].trim();
  return raw;
}

function polishLang(inner: string): string {
  let lang = normalizeOpenUILang(inner.trim());
  lang = convertNamedResearchView(lang);
  if (/^ResearchView\s*\(/i.test(lang)) {
    lang = `root = ${lang}`;
  }
  return lang;
}

/** Pull OpenUI Lang out of an assistant message body. */
export function extractOpenUILang(text: string): string | null {
  const raw = unwrapResearchText(text.replace(/\r\n/g, "\n"));
  const marked = raw.match(/---openui\s*\n([\s\S]*?)(?:\n---end\b|$)/i);
  if (marked?.[1]) {
    const inner = marked[1].replace(/\n---end\s*$/i, "").trim();
    if (inner) return polishLang(inner);
  }
  const trimmed = raw.trim();
  if (/^root\s*=/.test(trimmed)) {
    return polishLang(trimmed.replace(/\n---end\s*$/i, "").trim());
  }
  if (/ResearchView\s*\(/i.test(trimmed)) {
    return polishLang(trimmed);
  }
  return null;
}

export function isResearchBody(text: string): boolean {
  const raw = text.replace(/\r\n/g, "\n");
  return (
    /---openui/i.test(raw) ||
    /```openui/i.test(raw) ||
    /^root\s*=/.test(raw.trim()) ||
    /ResearchView\s*\(/i.test(raw)
  );
}

export function markdownFallback(text: string): string {
  const raw = text.replace(/\r\n/g, "\n");
  const marked = raw.match(/---openui[\s\S]*?---end\s*\n?([\s\S]*)$/i);
  if (marked?.[1]?.trim()) {
    return marked[1].trim();
  }
  const fenced = raw.match(/```(?:openui|openui-lang)[\s\S]*?```\s*\n?([\s\S]*)$/i);
  if (fenced?.[1]?.trim()) {
    return fenced[1].trim();
  }
  if (isResearchBody(raw)) {
    return "";
  }
  return raw;
}
