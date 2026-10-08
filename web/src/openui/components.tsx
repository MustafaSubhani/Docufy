import { defineComponent } from "@openuidev/react-lang";
import { z } from "zod";
import { ResearchViewPanel } from "./ResearchViewPanel";

const insightSchema = z.object({
  problem: z.string().describe("One or two sentences: the operational or business pain"),
  aiUse: z.string().describe("One or two sentences: how AI is applied in practice"),
  caveat: z.string().describe("One sentence: limits, risks, or data needs"),
});

const sourceSchema = z.object({
  title: z.string(),
  url: z.string(),
});

export const researchViewSchema = z.object({
  ask: z.string().describe("One or two sentences restating the question and scope"),
  context: z.string().describe("Three to five sentences of industry context from the pages, with specifics"),
  insights: z.array(insightSchema).describe("Five to eight rows when the notes support them"),
  gaps: z.string().describe("Two to four sentences on what the pages did not cover or left uncertain"),
  sources: z.array(sourceSchema).describe("Reference links from the page notes"),
});

export const ResearchView = defineComponent({
  name: "ResearchView",
  description: "Full research answer layout for Docufy",
  props: researchViewSchema,
  component: (render) => <ResearchViewPanel {...render.props} />,
});
