import type { FeedItem, Run, RunEventItem } from "./types";

const META = new Set(["name", "call_id", "agent", "run_id", "system_id", "status", "tier"]);

/** A run's stored timeline as feed lines, for a card opened after the events went by. The last
 * call of an agent that is still using a tool is the one in flight. */
export function feedFromEvents(events: RunEventItem[], run: Run): FeedItem[] {
  const items: FeedItem[] = [];
  events.forEach((e, i) => {
    const args = Object.fromEntries(Object.entries(e.data).filter(([k]) => !META.has(k)));
    if (e.type === "tool.call") {
      items.push({
        id: String(e.data.call_id ?? `ev${i}`),
        kind: "tool",
        agent: e.agent,
        name: String(e.data.name ?? "tool"),
        args,
        status: "done",
        at: e.at,
        stored: true,
      });
    } else if (e.type === "agent.status" && ["running", "done", "failed"].includes(String(e.data.status))) {
      const status = String(e.data.status);
      items.push({
        id: `ev${i}`,
        kind: "agent",
        agent: e.agent,
        name: status,
        args,
        status: status === "running" ? "running" : status === "failed" ? "failed" : "done",
        at: e.at,
      });
    }
  });
  const lastTool = new Map<string, FeedItem>();
  for (const item of items) if (item.kind === "tool" && item.agent) lastTool.set(item.agent, item);
  for (const [agent, item] of lastTool) if (run.agents[agent]?.status === "working") item.status = "running";
  return items;
}
