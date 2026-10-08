import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { researchSystemPrompt } from "../src/openui/library";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");
const prompt = researchSystemPrompt();
const out = join(root, "spark_agent", "prompts", "research_openui_prompt.txt");
writeFileSync(out, prompt, "utf8");
console.log(`Wrote ${out} (${prompt.length} chars)`);
