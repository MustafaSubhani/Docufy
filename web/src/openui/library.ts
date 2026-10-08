import { createLibrary } from "@openuidev/react-lang";
import { ResearchView } from "./components";

export const researchLibrary = createLibrary({
  root: "ResearchView",
  components: [ResearchView],
});

export function researchSystemPrompt(): string {
  return researchLibrary.prompt({
    preamble:
      "You are Docufy's research agent. Answer only from the supplied page notes. " +
      "Do not invent organisations, products, or figures. Use UK English.",
    additionalRules: [
      "Reply with OpenUI Lang only between ---openui and ---end lines (each on its own line).",
      "Set root = ResearchView(...) with ask, context, insights (array of objects), gaps, and sources (array of title and url).",
      "In objects use colons only: {problem: \"...\", aiUse: \"...\"}. Never write problem = inside braces.",
      "Never wrap the answer in markdown code fences. Never use ask: inside ResearchView(...); use positional arguments only.",
      "Every source url must appear in the page notes.",
      "Use five to eight insights when the notes support them; write full sentences, not labels.",
      "Never give a thin or bullet-only answer when the page notes contain more detail.",
    ],
  });
}
