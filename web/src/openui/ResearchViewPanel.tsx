import type { ResearchViewProps } from "./types";

export function ResearchViewPanel({ ask, context, insights, gaps, sources }: ResearchViewProps) {
  return (
    <div className="oui-research">
      <section className="oui-block">
        <h3 className="oui-heading">What you asked</h3>
        <p className="oui-prose">{ask}</p>
      </section>
      <section className="oui-block">
        <h3 className="oui-heading">Context</h3>
        <p className="oui-prose">{context}</p>
      </section>
      <section className="oui-block">
        <h3 className="oui-heading">Common problems and where AI helps</h3>
        <div className="oui-insights">
          {insights.map((row, index) => (
            <div className="oui-insight" key={index}>
              <p className="oui-insight-problem">{row.problem}</p>
              <p className="oui-insight-ai">{row.aiUse}</p>
              <p className="oui-insight-caveat">{row.caveat || "Validate against your own data and constraints."}</p>
            </div>
          ))}
        </div>
      </section>
      <aside className="oui-callout">
        <p className="oui-callout-title">Gaps</p>
        <p>{gaps}</p>
      </aside>
      <section className="oui-block">
        <h3 className="oui-heading">References</h3>
        <div className="oui-sources">
          {sources.map((item, index) => (
            <a className="oui-source" href={item.url} target="_blank" rel="noreferrer" key={index}>
              {item.title}
            </a>
          ))}
        </div>
      </section>
    </div>
  );
}
