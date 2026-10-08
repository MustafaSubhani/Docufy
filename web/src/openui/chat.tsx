import { createLibrary, defineComponent } from "@openuidev/react-lang";
import { createContext, useContext } from "react";
import { z } from "zod";
import { ArrowUp } from "../icons";
import { ResearchView } from "./components";

/** Lets an Actions button send its prompt as the next message. */
export const ChatActions = createContext<{ send: (text: string) => void; busy: boolean }>({ send: () => undefined, busy: false });

const tone = z.enum(["note", "risk", "success"]);

export const Callout = defineComponent({
  name: "Callout",
  description: "A boxed point: a decision, a risk, a caveat, or a result worth stressing",
  props: z.object({
    tone: tone.describe("note for neutral, risk for a warning, success for a result"),
    title: z.string(),
    body: z.string().describe("One to three sentences. Inline markdown links allowed"),
  }),
  component: ({ props }) => (
    <aside className={`oc-callout ${props.tone}`}>
      <p className="oc-callout-title">{props.title}</p>
      <p>{props.body}</p>
    </aside>
  ),
});

const fact = z.object({
  label: z.string(),
  value: z.string().describe("The figure or short answer, as stated in a source"),
  note: z.string().describe("Where it comes from or what it means, one short line"),
});

export const KeyFacts = defineComponent({
  name: "KeyFacts",
  description: "Two to six headline figures or answers, each with its source or meaning",
  props: z.object({ items: z.array(fact) }),
  component: ({ props }) => (
    <div className="oc-facts">
      {props.items.map((item, index) => (
        <div className="oc-fact" key={index}>
          <span className="oc-fact-value">{item.value}</span>
          <span className="oc-fact-label">{item.label}</span>
          {item.note ? <span className="oc-fact-note">{item.note}</span> : null}
        </div>
      ))}
    </div>
  ),
});

export const Compare = defineComponent({
  name: "Compare",
  description: "A comparison table: options against criteria",
  props: z.object({
    columns: z.array(z.string()).describe("Header cells, first is the row label"),
    rows: z.array(z.array(z.string())).describe("Each row has one cell per column"),
  }),
  component: ({ props }) => (
    <div className="oc-table-wrap">
      <table className="oc-table">
        <thead>
          <tr>{props.columns.map((cell, index) => <th key={index}>{cell}</th>)}</tr>
        </thead>
        <tbody>
          {props.rows.map((row, index) => (
            <tr key={index}>{row.map((cell, i) => <td key={i}>{cell}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  ),
});

const step = z.object({ title: z.string(), detail: z.string() });

export const Steps = defineComponent({
  name: "Steps",
  description: "An ordered plan or process, three to seven steps",
  props: z.object({ items: z.array(step) }),
  component: ({ props }) => (
    <ol className="oc-steps">
      {props.items.map((item, index) => (
        <li key={index}>
          <span className="oc-step-no">{index + 1}</span>
          <div>
            <p className="oc-step-title">{item.title}</p>
            <p className="oc-step-detail">{item.detail}</p>
          </div>
        </li>
      ))}
    </ol>
  ),
});

const section = z.object({ heading: z.string(), summary: z.string().describe("What the section covers, one line") });

export const Outline = defineComponent({
  name: "Outline",
  description: "A proposed document outline before writing, so the person can confirm or change it",
  props: z.object({
    title: z.string(),
    audience: z.string().describe("Who reads it, e.g. client leadership, internal delivery team"),
    sections: z.array(section),
  }),
  component: ({ props }) => (
    <div className="oc-outline">
      <p className="oc-outline-kicker">Proposed outline · {props.audience}</p>
      <p className="oc-outline-title">{props.title}</p>
      <ol>
        {props.sections.map((item, index) => (
          <li key={index}>
            <span className="oc-outline-no">{String(index + 1).padStart(2, "0")}</span>
            <span>
              <strong>{item.heading}</strong>
              <em>{item.summary}</em>
            </span>
          </li>
        ))}
      </ol>
    </div>
  ),
});

const source = z.object({ title: z.string(), url: z.string(), note: z.string().describe("What this source supports") });

export const Sources = defineComponent({
  name: "Sources",
  description: "The sources behind an answer. Every url must come from a search or a page you read",
  props: z.object({ items: z.array(source) }),
  component: ({ props }) => (
    <div className="oc-sources">
      {props.items.map((item, index) => (
        <a className="oc-source" href={item.url} target="_blank" rel="noreferrer" key={index}>
          <span className="oc-source-host">{host(item.url)}</span>
          <span className="oc-source-title">{item.title}</span>
          {item.note ? <span className="oc-source-note">{item.note}</span> : null}
        </a>
      ))}
    </div>
  ),
});

const action = z.object({
  label: z.string().describe("Two to five words on the button"),
  prompt: z.string().describe("The full message sent when it is pressed"),
});

function ActionsView({ items }: { items: { label: string; prompt: string }[] }) {
  const chat = useContext(ChatActions);
  return (
    <div className="oc-actions">
      {items.map((item, index) => (
        <button key={index} type="button" className="oc-action" disabled={chat.busy} onClick={() => chat.send(item.prompt)}>
          {item.label}
          <ArrowUp size={13} />
        </button>
      ))}
    </div>
  );
}

export const Actions = defineComponent({
  name: "Actions",
  description: "Two to four next steps the person can take with one click",
  props: z.object({ items: z.array(action) }),
  component: ({ props }) => <ActionsView items={props.items} />,
});

export const Panel = defineComponent({
  name: "Panel",
  description: "The container for structured chat content. Children render top to bottom",
  props: z.object({
    children: z.array(
      z.union([Callout.ref, KeyFacts.ref, Compare.ref, Steps.ref, Outline.ref, Sources.ref, Actions.ref, ResearchView.ref]),
    ),
  }),
  component: ({ props, renderNode }) => <div className="oc-panel">{renderNode(props.children)}</div>,
});

export const chatLibrary = createLibrary({
  root: "Panel",
  components: [Panel, Callout, KeyFacts, Compare, Steps, Outline, Sources, Actions, ResearchView],
  componentGroups: [
    { name: "Layout", components: ["Panel"] },
    { name: "Content", components: ["Callout", "KeyFacts", "Compare", "Steps", "Outline", "ResearchView"] },
    { name: "Navigation", components: ["Sources", "Actions"], notes: ["End a substantial answer with Actions when there is an obvious next step."] },
  ],
});

export function chatSystemPrompt(): string {
  return chatLibrary.prompt({
    preamble:
      "Docufy chat replies are Markdown. When structure helps, add ONE openui-lang block built from these components. " +
      "The block sits inside the Markdown reply; text before and after it is shown as normal chat.",
    inlineMode: true,
    additionalRules: [
      "Use at most one openui-lang fenced block per reply, and only when it adds structure: findings, figures, a comparison, an outline, sources, or next actions.",
      "Never put a document or a draft inside a block. Drafts go to the canvas with create_draft.",
      "Arguments are positional. Objects use colons: {label: \"...\", value: \"...\"}.",
      "Every url in Sources must come from web search results or a page you read in this turn or earlier in the chat.",
      "KeyFacts values must be quoted from sources or the person's own material, never estimated.",
      "Use Outline to propose a structure before a long document when the brief is thin; add Actions such as Write it.",
      "Actions prompts are full sentences written as the person, e.g. \"Set the draft in the Spark template with the company pages\".",
    ],
    examples: [
      'Here is what the sources agree on.\n\n```openui-lang\nroot = Panel([facts, src, next])\nfacts = KeyFacts([{label: "Fleet size", value: "120 vans", note: "From your brief"}])\nsrc = Sources([{title: "Fleet telematics study", url: "https://example.com/study", note: "Adoption rates"}])\nnext = Actions([{label: "Write the proposal", prompt: "Write the client proposal from this research"}])\n```',
    ],
  });
}

function host(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url.slice(0, 40);
  }
}
