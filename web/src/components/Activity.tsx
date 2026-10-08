import { useEffect, useState } from "react";
import type { Step } from "../api";
import { Check, ChevronDown, Close, Minus, Spinner } from "../icons";

type Props = {
  steps: Step[];
  live: boolean;
  fallback: string;
  outcome?: "ready" | "failed" | "stopped" | "blocked" | "writing";
  onStop?: () => void;
};

/** The steps a job took. Open while it runs, folded into one line when it is done. */
export function Activity({ steps, live, fallback, outcome, onStop }: Props) {
  const [open, setOpen] = useState(live);
  const now = useNow(live);
  useEffect(() => setOpen(live), [live]);

  const shown = steps.map((step) => (!live && step.status === "running" ? { ...step, status: "stopped" as const } : step));
  const top = shown.filter((step) => step.kind === "step");
  const children = (key: string) => shown.filter((step) => step.parent === key);
  const agents = shown.filter((step) => step.kind === "agent").length;
  const running = [...top].reverse().find((step) => step.status === "running");
  const first = shown.length ? Math.min(...shown.map((step) => step.started)) : now;
  const last = live ? now : Math.max(first, ...shown.map((step) => step.ended ?? step.started));
  const seconds = Math.max(0, last - first);
  const summary = live
    ? open && top.length
      ? "Working"
      : running?.label || fallback
    : outcome === "stopped"
      ? `Stopped after ${duration(seconds)}`
      : outcome === "failed"
        ? `Stopped with a problem after ${duration(seconds)}`
        : `Worked for ${duration(seconds)}`;
  const counts = [top.length ? `${top.length} ${top.length === 1 ? "step" : "steps"}` : "", agents ? `${agents} ${agents === 1 ? "agent" : "agents"}` : ""]
    .filter(Boolean)
    .join(", ");

  return (
    <div className={live ? "activity live" : "activity"}>
      <div className="activity-head">
        <button type="button" className="activity-toggle" aria-expanded={open} onClick={() => setOpen((value) => !value)} disabled={!top.length}>
          <span className={`activity-mark ${live ? "running" : outcome === "failed" ? "failed" : outcome === "stopped" ? "stopped" : "done"}`}>
            {live ? <Spinner size={13} /> : outcome === "failed" || outcome === "stopped" ? <Minus size={12} /> : <Check size={12} />}
          </span>
          <span className="activity-summary" key={summary}>
            {summary}
          </span>
          {counts ? <span className="activity-count">{live ? duration(seconds) : counts}</span> : null}
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
              <li key={step.key} className={`tl-step ${step.status}`}>
                <StepRow step={step} now={now} />
                {children(step.key).length ? (
                  <ul className="tl-children">
                    {children(step.key).map((child) => (
                      <li key={child.key} className={`tl-child ${child.status}`}>
                        <StepRow step={child} now={now} />
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

function StepRow({ step, now }: { step: Step; now: number }) {
  const took = (step.ended ?? now) - step.started;
  return (
    <div className="tl-row">
      <span className={`tl-node ${step.status}`} aria-hidden="true">
        {step.status === "running" ? <Spinner size={11} /> : step.status === "done" ? <Check size={11} /> : step.status === "failed" ? <Close size={11} /> : <Minus size={11} />}
      </span>
      {step.kind === "agent" ? <span className="agent-tag">Agent</span> : null}
      <span className="tl-label">{step.label}</span>
      {step.detail ? <span className="tl-detail">{step.detail}</span> : null}
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

function duration(seconds: number) {
  const value = Math.max(0, seconds);
  if (value < 10) return `${value.toFixed(1)}s`;
  if (value < 60) return `${Math.round(value)}s`;
  const minutes = Math.floor(value / 60);
  return `${minutes}m ${Math.round(value % 60)}s`;
}
