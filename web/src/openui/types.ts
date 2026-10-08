export type ResearchInsight = { problem: string; aiUse: string; caveat?: string };
export type ResearchSource = { title: string; url: string };

export type ResearchViewProps = {
  ask: string;
  context: string;
  insights: ResearchInsight[];
  gaps: string;
  sources: ResearchSource[];
};
