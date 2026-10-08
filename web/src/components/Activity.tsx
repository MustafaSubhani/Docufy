import { useEffect, useState } from "react";
import type { Step } from "../api";
import { Check, ChevronDown, Close, Minus, Spinner } from "../icons";

type Props = {
  steps: Step[];
  live: boolean;
  fallback: string;
  outcome?: "ready" | "failed" | "stopped" | "blocked" | "writing";
  onStop?: () => void;
  compact?: boolean;
};

const TOP = new Set(["step", "tool", "thought"]);

/** The steps a job took. Open while it runs, folded into one line when it is done. */
export function Activity({ steps, live, fallback, outcome, onStop, compact }: Props) {
  const [open, setOpen] = useState(live && !compact);
  const now = useNow(live);
  useEffect(() => setOpen(live && !compact), [live, compact]);

  const shown = steps.map((step) => (!live && step.status === "running" ? { ...step, status: "stopped" as const } : step));
  const top = shown.filter((step) => TOP.has(step.kind) && !step.parent);
  const children = (key: string) => shown.filter((step) => step.parent === key);
  const tools = shown.filter((step) => step.kind === "tool").length;
  const thoughts = shown.filter((step) => step.kind === "thought").length;
  const agents = shown.filter((step) => step.kind === "agent").length;
  const running = [...shown].reverse().find((step) => step.status === "running" && step.kind !== "item");
  const first = shown.length ? Math.min(...shown.map((step) => step.started)) : now;
  const last = live ? now : Math.max(first, ...shown.map((step) => step.ended ?? step.started));
  const seconds = Math.max(0, last - first);
  const summary = live
    ? running?.label
      ? running.detail && running.kind === "tool"
        ? `${running.label}: ${running.detail}`
        : running.label
      : fallback || "Working"
    : outcome === "stopped"
      ? `Stopped after ${duration(seconds)}`
      : outcome === "failed"
        ? `Stopped with a problem after ${duration(seconds)}`
        : `Worked for ${duration(seconds)}`;
  const counts = [
    tools ? `${tools} ${tools === 1 ? "tool call" : "tool calls"}` : "",
    thoughts && !tools ? `${thoughts} ${thoughts === 1 ? "thought" : "thoughts"}` : "",
    !tools && top.length ? `${top.length} ${top.length === 1 ? "step" : "steps"}` : "",
    agents ? `${agents} ${agents === 1 ? "agent" : "agents"}` : "",
  ]
    .filter(Boolean)
    .join(", ");

  if (!top.length && !live) return null;

  return (
    <div className={live ? "activity live" : "activity"}>
      <div className="activity-head">
        <button type="button" className="activity-toggle" aria-expanded={open} onClick={() => setOpen((value) => !value)} disabled={!top.length}>
          <span className={`activity-mark ${live ? "running" : outcome === "failed" ? "failed" : outcome === "stopped" ? "stopped" : "done"}`}>
            {live ? <Spinner size={13} /> : outcome === "failed" || outcome === "stopped" ? <Minus size={12} /> : <Check size={12} />}
          </span>
          <span className={live ? "activity-summary shimmer" : "activity-summary"} key={summary}>
            {summary}
          </span>
          {counts || live ? <span className="activity-count">{live ? duration(seconds) : counts}</span> : null}
          {top.length ? <ChevronDown size={15} className={open ? "chev open" : "chev"} /> : null}
        </button>
        {live && onStop ? (
          <button type="button" className="text-button" onClick={onStop}>
            Stop
          </button>
        ) : null}
      </div>
      <div className={open && top.length ? "collapse open" : "collapse"}>
        <div className="collapse-inner">
          <ol className="timeline">
            {top.map((step) => (
              <li key={step.key} className={`tl-step ${step.status} k-${step.kind}`}>
                {step.kind === "thought" ? <Thought step={step} now={now} /> : <StepRow step={step} now={now} />}
                {children(step.key).length ? (
                  <ul className="tl-children">
                    {children(step.key).map((child) => (
                      <li key={child.key} className={`tl-child ${child.status}`}>
                        <StepRow step={child} now={now} />
                        {children(child.key).length ? (
                          <ul className="tl-children inner">
                            {children(child.key).map((grand) => (
                              <li key={grand.key} className={`tl-child ${grand.status}`}>
                                <StepRow step={grand} now={now} />
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}

function Thought({ step, now }: { step: Step; now: number }) {
  const [open, setOpen] = useState(false);
  const took = (step.ended ?? now) - step.started;
  const text = step.detail.replace(/\*\*/g, "");
  const headline = text.split("\n").find((line) => line.trim())?.slice(0, 90) || "Thinking";
  return (
    <div className="tl-thought">
      <button type="button" className="tl-row tl-thought-toggle" aria-expanded={open} onClick={() => setOpen((value) => !value)} disabled={!text}>
        <span className={`tl-node ${step.status}`} aria-hidden="true">
          {step.status === "running" ? <Spinner size={11} /> : <Dot />}
        </span>
        <span className="tl-label">{step.status === "running" ? "Thinking" : headline}</span>
        <span className="tl-time">{step.status === "running" || took >= 0.5 ? duration(took) : ""}</span>
      </button>
      {open && text ? <p className="tl-thought-text">{text}</p> : null}
    </div>
  );
}

function Dot() {
  return <span className="tl-dot" />;
}

function StepRow({ step, now }: { step: Step; now: number }) {
  const took = (step.ended ?? now) - step.started;
  return (
    <div className="tl-row">
      <span className={`tl-node ${step.status}`} aria-hidden="true">
        {step.status === "running" ? <Spinner size={11} /> : step.status === "done" ? <Check size={11} /> : step.status === "failed" ? <Close size={11} /> : <Minus size={11} />}
      </span>
      {step.kind === "agent" ? <span className="agent-tag">Agent</span> : null}
      {step.kind === "tool" && step.label.includes(": ") ? <span className="agent-tag mcp">MCP</span> : null}
      <span className="tl-label">{step.label}</span>
      {step.detail ? <span className="tl-detail" title={step.detail}>{step.detail}</span> : null}
      <span className="tl-time">{step.status === "running" || took >= 0.5 ? duration(took) : ""}</span>
    </div>
  );
}

function useNow(live: boolean) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!live) return;
    setNow(Date.now() / 1000);
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 500);
    return () => window.clearInterval(timer);
  }, [live]);
  return now;
}

export function duration(seconds: number) {
  const value = Math.max(0, seconds);
  if (value < 10) return `${value.toFixed(1)}s`;
  if (value < 60) return `${Math.round(value)}s`;
  const minutes = Math.floor(value / 60);
  return `${minutes}m ${Math.round(value % 60)}s`;
}
