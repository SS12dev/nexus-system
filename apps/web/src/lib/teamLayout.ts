import type { AgentNode, Topology } from "./types";

/** Width of the team diagram, in its own units (the chat card's SVG uses these as pixels). */
export const TEAM_W = 520;

/** Where each agent sits in a team's diagram: the lead above its workers for a supervisor, a
 * software team's stages left to right with its two builders stacked, otherwise in a row.
 * Shared by the chat card's diagram and the orb, so both show the same shape. */
export function teamLayout(nodes: AgentNode[], topology: Topology) {
  const W = TEAM_W;
  const pos: Record<string, { x: number; y: number }> = {};
  if (topology === "dev_project") {
    const stages = [...new Set(nodes.map((n) => n.stage ?? n.id))];
    stages.forEach((st, col) => {
      const here = nodes.filter((n) => (n.stage ?? n.id) === st);
      here.forEach((n, row) => {
        pos[n.id] = {
          x: ((col + 1) * W) / (stages.length + 1),
          y: here.length > 1 ? 50 + row * 90 : 95,
        };
      });
    });
    return { pos, height: 200 };
  }
  if (topology === "supervisor_workers" && nodes.length > 1) {
    pos[nodes[0].id] = { x: W / 2, y: 40 };
    const workers = nodes.slice(1);
    workers.forEach((n, i) => {
      pos[n.id] = { x: ((i + 1) * W) / (workers.length + 1), y: 140 };
    });
    return { pos, height: 190 };
  }
  nodes.forEach((n, i) => {
    pos[n.id] = { x: ((i + 1) * W) / (nodes.length + 1), y: 60 };
  });
  return { pos, height: 120 };
}
