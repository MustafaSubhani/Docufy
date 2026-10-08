import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chatSystemPrompt } from "../src/openui/chat";
import { researchSystemPrompt } from "../src/openui/library";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..", "spark_agent", "prompts");

for (const [name, prompt] of [
  ["research_openui_prompt.txt", researchSystemPrompt()],
  ["chat_openui_prompt.txt", chatSystemPrompt()],
] as const) {
  const out = join(root, name);
  writeFileSync(out, prompt, "utf8");
  console.log(`Wrote ${out} (${prompt.length} chars)`);
}
