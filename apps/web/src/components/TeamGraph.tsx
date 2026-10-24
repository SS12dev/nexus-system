import { TEAM_W, teamLayout } from "../lib/teamLayout";
import type { AgentEdge, AgentLive, AgentNode, Topology } from "../lib/types";

const STATUS_FILL: Record<string, string> = {
  idle: "#334155",
  running: "#0ea5e9",
  thinking: "#0ea5e9",
  working: "#f59e0b",
  waiting: "#f43f5e",
  done: "#10b981",
  failed: "#ef4444",
  skipped: "#1e293b",
};

const W = TEAM_W;
const NODE_R = 22;

export function TeamGraph({
  nodes,
  edges,
  topology,
  live = {},
  compact = false,
}: {
  nodes: AgentNode[];
  edges: AgentEdge[];
  topology: Topology;
  live?: Record<string, AgentLive>;
  compact?: boolean;
}) {
  const { pos, height } = teamLayout(nodes, topology);
  const curved = topology === "critic_loop";
  // A software team's fixes loop back from the reviewer to the builders, under the row.
  const loops = topology === "dev_project";
  return (
    <svg
      viewBox={`0 0 ${W} ${height}`}
      className={compact ? "h-24 w-full" : "h-auto w-full"}
      role="img"
      aria-label="Agent team"
    >
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L10,5 L0,10 z" fill="#64748b" />
        </marker>
      </defs>
      {edges.map((e, i) => {
        const a = pos[e.from];
        const b = pos[e.to];
        if (!a || !b) return null;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const len = Math.hypot(dx, dy) || 1;
        const [ux, uy] = [dx / len, dy / len];
        const x1 = a.x + ux * NODE_R;
        const y1 = a.y + uy * NODE_R;
        const x2 = b.x - ux * (NODE_R + 4);
        const y2 = b.y - uy * (NODE_R + 4);
        const bend = curved ? (e.kind === "feedback" ? 30 : -30) : loops && e.kind === "feedback" ? 50 : 0;
        const mx = (x1 + x2) / 2 - uy * bend;
        const my = (y1 + y2) / 2 + ux * bend;
        return (
          <g key={i}>
            <path
              d={`M${x1},${y1} Q${mx},${my} ${x2},${y2}`}
              fill="none"
              stroke="#475569"
              strokeWidth={1.5}
              strokeDasharray={e.kind === "feedback" ? "4 3" : undefined}
              markerEnd="url(#arrow)"
            />
            {!compact && (
              <text x={mx} y={my - 6} textAnchor="middle" className="fill-slate-500 text-[10px]">
                {e.kind}
              </text>
            )}
          </g>
        );
      })}
      {nodes.map((n) => {
        const p = pos[n.id];
        const st = live[n.id]?.status ?? "idle";
        const active = ["running", "thinking", "working"].includes(st);
        return (
          <g key={n.id}>
            {active && (
              <circle cx={p.x} cy={p.y} r={NODE_R + 5} fill="none" stroke={STATUS_FILL[st]} opacity={0.5}>
                <animate attributeName="r" values={`${NODE_R + 2};${NODE_R + 8};${NODE_R + 2}`} dur="1.4s" repeatCount="indefinite" />
              </circle>
            )}
            <circle
              cx={p.x}
              cy={p.y}
              r={NODE_R}
              fill={STATUS_FILL[st] ?? STATUS_FILL.idle}
              stroke={n.lead ? "#e0f2fe" : "#1e293b"}
              strokeWidth={n.lead ? 2 : 1}
            >
              <title>
                {n.name} ({n.role}, {live[n.id]?.tier ?? n.tier}): {st}
                {live[n.id]?.tool ? `, ${live[n.id]?.tool}` : ""}
              </title>
            </circle>
            {topology === "parallel_map_reduce" && n === nodes[1] && (
              <text x={p.x + NODE_R} y={p.y - NODE_R + 4} className="fill-slate-300 text-[11px]">
                ×N
              </text>
            )}
            <text x={p.x} y={p.y + 4} textAnchor="middle" className="fill-white text-[11px] font-semibold">
              {n.name.slice(0, 2).toUpperCase()}
            </text>
            <text x={p.x} y={p.y + NODE_R + 14} textAnchor="middle" className="fill-slate-300 text-[11px]">
              {n.name}
            </text>
            {!compact && (
              <text x={p.x} y={p.y + NODE_R + 27} textAnchor="middle" className="fill-slate-500 text-[10px]">
                {n.stage ? `${n.stage} · ` : ""}
                {live[n.id]?.tier ?? n.tier}
                {live[n.id]?.tool && ["working", "waiting"].includes(st) ? ` · ${live[n.id]?.tool}` : ""}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
