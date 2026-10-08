import { createParser } from "@openuidev/lang-core";
import { researchLibrary } from "./library";
import { normalizeOpenUILang } from "./extract";
import type { ResearchViewProps } from "./types";

const parser = createParser(researchLibrary.toJSONSchema());

export function parseResearchView(lang: string): ResearchViewProps | null {
  const normalized = normalizeOpenUILang(lang.trim());
  try {
    const result = parser.parse(normalized);
    const root = result.root;
    if (!root || root.type !== "element" || root.typeName !== "ResearchView") {
      return null;
    }
    return root.props as ResearchViewProps;
  } catch {
    return null;
  }
}
