import { useId } from "react";
import type { AssistantState } from "../lib/types";
import { useAssistantName } from "../lib/assistant";

/** Nodes of the constellation inside the mark (a 100×100 box, sphere centred at 50,50, r 34):
 * a tilted ring of what the assistant knows around you at the centre. */
const RING: [number, number][] = [
  [50, 26], [74, 36], [76, 60], [56, 74], [30, 66], [24, 42],
];
// Chords between ring nodes, so it reads as a network rather than a wheel.
const CHORDS: [number, number][] = [[0, 2], [1, 3], [2, 4], [3, 5], [5, 1]];

/** Nexus's mark: a glass sphere holding a small constellation, with two satellites on an
 * outer orbit (the teams and routines that work outside it), like the 3D orb on the home
 * screen. Its glow and motion follow the assistant's state. */
export function NexusMark({
  state = "idle",
  size = 28,
  label,
}: {
  state?: AssistantState;
  size?: number;
  label?: string;
}) {
  const id = useId().replace(/:/g, "");
  const detail = size >= 40;
  return (
    <svg
      viewBox="0 0 100 100"
      width={size}
      height={size}
      className="nexus-mark shrink-0"
      data-state={state}
      role="img"
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <defs>
        <radialGradient id={`${id}-glass`} cx="42%" cy="36%" r="70%">
          <stop offset="0" stopColor="#38bdf8" stopOpacity="0.55" />
          <stop offset="0.55" stopColor="#1d4ed8" stopOpacity="0.35" />
          <stop offset="1" stopColor="#0b1020" stopOpacity="0.9" />
        </radialGradient>
        <radialGradient id={`${id}-core`} cx="50%" cy="45%" r="50%">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.5" stopColor="#bae6fd" />
          <stop offset="1" stopColor="#38bdf8" />
        </radialGradient>
        <linearGradient id={`${id}-rim`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#7dd3fc" />
          <stop offset="1" stopColor="#6366f1" />
        </linearGradient>
      </defs>
      {/* Outer orbit with two satellites: what works outside the orb. */}
      <g className="nexus-mark-orbit">
        <ellipse cx="50" cy="50" rx="46" ry="17" fill="none" stroke={`url(#${id}-rim)`} strokeOpacity="0.45" strokeWidth="1.6" transform="rotate(-24 50 50)" />
        <circle cx="91" cy="35" r="3.6" fill="#a5b4fc" />
        <circle cx="9" cy="65" r="2.8" fill="#7dd3fc" />
      </g>
      {/* The sphere. */}
      <circle cx="50" cy="50" r="34" fill={`url(#${id}-glass)`} />
      <circle cx="50" cy="50" r="34" fill="none" stroke={`url(#${id}-rim)`} strokeWidth={detail ? 1.6 : 2.4} strokeOpacity="0.9" />
      {/* The constellation inside: spokes to the centre, chords between nodes. */}
      <g className="nexus-mark-inner">
        {RING.map(([x, y], i) => (
          <line key={`s${i}`} x1="50" y1="50" x2={x} y2={y} stroke="#7dd3fc" strokeOpacity="0.55" strokeWidth={detail ? 1 : 1.6} />
        ))}
        {detail &&
          CHORDS.map(([a, b], i) => (
            <line key={`c${i}`} x1={RING[a][0]} y1={RING[a][1]} x2={RING[b][0]} y2={RING[b][1]} stroke="#a5b4fc" strokeOpacity="0.35" strokeWidth="0.8" />
          ))}
        {RING.map(([x, y], i) => (
          <circle key={`n${i}`} cx={x} cy={y} r={i % 2 ? 2.6 : 3.4} fill={i % 2 ? "#a5b4fc" : "#e0f2fe"} />
        ))}
      </g>
      <circle className="nexus-mark-core" cx="50" cy="50" r="7.5" fill={`url(#${id}-core)`} />
      {/* A soft highlight, for the glass. */}
      {detail && <ellipse cx="38" cy="30" rx="11" ry="5" fill="#ffffff" fillOpacity="0.12" transform="rotate(-30 38 30)" />}
    </svg>
  );
}

export function Orb({ state, size = 28 }: { state: AssistantState; size?: number }) {
  const assistant = useAssistantName();
  return <NexusMark state={state} size={size} label={`${assistant} is ${state}`} />;
}
