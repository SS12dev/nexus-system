import { Html, OrbitControls, Stars } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Bloom, EffectComposer } from "@react-three/postprocessing";
import { useEffect, useRef } from "react";
import type { AssistantState } from "../lib/types";
import { CoreOrb } from "./CoreOrb";
import { Constellation, Crystals, MemoryCloud } from "./Inside";
import {
  cameraDistance,
  type Crystal,
  type Inside,
  type MemoryPoint,
  type OrbCluster,
  type OrbNode,
  type Satellite,
  type StarPoint,
} from "./model";
import { Network } from "./Network";
import { Satellites } from "./Satellites";
import type { Perf } from "./perf";

export type Selection =
  | { type: "agent"; node: OrbNode }
  | { type: "memory"; point: MemoryPoint }
  | { type: "artifact"; crystal: Crystal }
  | { type: "entity"; star: StarPoint }
  | { type: "satellite"; sat: Satellite };

export function selectionId(s: Selection | null): string | null {
  if (!s) return null;
  if (s.type === "agent") return s.node.key;
  if (s.type === "memory") return s.point.id;
  if (s.type === "entity") return s.star.id;
  if (s.type === "satellite") return s.sat.id;
  return s.crystal.id;
}

/** The whole 3D home scene. Lazy-loaded, so three.js only downloads on the orb page. */
export default function OrbScene({
  assistantState,
  activity = null,
  clusters,
  inside,
  sats = [],
  lit,
  litSeq,
  labels,
  tags,
  perf,
  selected,
  onSelect,
}: {
  assistantState: AssistantState;
  /** What Nexus is doing right now, shown under the orb while it works. */
  activity?: string | null;
  clusters: OrbCluster[];
  /** The constellation, memories and artifacts inside the orb. */
  inside: Inside;
  /** Routines and projects on the ring outside it. */
  sats?: Satellite[];
  /** What the latest reply recalled; seq changes each time it lights up. */
  lit: Set<string>;
  litSeq: number;
  /** How many star names to show; fewer in the small chat-side orb. */
  labels: number;
  /** The star name elements (StarLabels, drawn over the canvas), for the scene to place. */
  tags: Map<string, HTMLElement>;
  perf: Perf;
  selected: Selection | null;
  onSelect: (s: Selection | null) => void;
}) {
  const still = perf.reducedMotion;
  const capped = perf.lite || still;
  const selId = selectionId(selected);
  return (
    <Canvas
      dpr={perf.lite ? [1, 1.5] : [1, 2]}
      camera={{ position: [0, 1.4, 6], fov: 50, near: 0.1, far: 200 }}
      frameloop={capped ? "demand" : "always"}
      gl={{ antialias: !perf.lite, powerPreference: perf.lite ? "low-power" : "high-performance" }}
      onPointerMissed={() => onSelect(null)}
    >
      <color attach="background" args={["#05070d"]} />
      <ambientLight intensity={0.5} />
      <pointLight position={[0, 0, 0]} intensity={6} distance={12} color="#7dd3fc" />
      <directionalLight position={[4, 6, 5]} intensity={0.8} />
      <Stars radius={60} depth={40} count={perf.stars} factor={3} fade speed={still ? 0 : 0.4} />

      <CoreOrb state={assistantState} still={still} />
      {activity && (
        <Html center position={[0, -1.3, 0]} zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
          <div className="orb-label orb-label-activity">{activity}</div>
        </Html>
      )}
      <Constellation
        stars={inside.stars}
        lines={inside.lines}
        crystals={inside.crystals}
        still={still}
        lite={perf.lite}
        selected={selId}
        lit={lit}
        litSeq={litSeq}
        labels={labels}
        tags={tags}
        onPick={(star) => onSelect({ type: "entity", star })}
      />
      <MemoryCloud
        points={inside.memories}
        still={still}
        lite={perf.lite}
        selected={selId}
        lit={lit}
        onPick={(point) => onSelect({ type: "memory", point })}
      />
      <Crystals
        items={inside.crystals}
        still={still}
        selected={selId}
        onPick={(crystal) => onSelect({ type: "artifact", crystal })}
      />
      <Satellites
        items={sats}
        stars={inside.stars}
        still={still}
        selected={selId}
        onPick={(sat) => onSelect({ type: "satellite", sat })}
      />
      <Network
        clusters={clusters}
        still={still}
        lite={perf.lite}
        selected={selId}
        onPick={(node) => onSelect({ type: "agent", node })}
      />

      <CameraRig teams={clusters.length} compact={perf.portrait} still={still} />
      <OrbitControls
        makeDefault
        enablePan={false}
        enableDamping={!still}
        minDistance={1.8}
        maxDistance={30}
        autoRotate={!still}
        autoRotateSpeed={0.35}
      />
      {!perf.lite && (
        <EffectComposer multisampling={0}>
          <Bloom intensity={0.9} luminanceThreshold={0.15} luminanceSmoothing={0.3} mipmapBlur />
        </EffectComposer>
      )}
      {capped && <FrameCap fps={still ? 10 : 30} />}
    </Canvas>
  );
}

/** Step the camera back (or in) when teams come and go, so the network stays in view. Only
 * runs when the number of teams changes, so it never fights the user's own zoom. */
function CameraRig({ teams, compact, still }: { teams: number; compact: boolean; still: boolean }) {
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  const goal = useRef<number | null>(null);
  const aspect = size.width / Math.max(size.height, 1);

  useEffect(() => {
    goal.current = cameraDistance(teams, aspect, compact);
  }, [teams, aspect, compact]);

  useFrame((_, dt) => {
    if (goal.current == null) return;
    const d = camera.position.length();
    const next = still ? goal.current : d + (goal.current - d) * (1 - Math.exp(-dt * 2));
    camera.position.setLength(next);
    if (Math.abs(next - goal.current) < 0.01) goal.current = null;
  });
  return null;
}

/** On phones and with reduced motion the scene renders on demand; this ticks it at a fixed
 * rate instead of the display's full refresh rate, to save battery. */
function FrameCap({ fps }: { fps: number }) {
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => {
    const id = window.setInterval(() => invalidate(), 1000 / fps);
    return () => window.clearInterval(id);
  }, [fps, invalidate]);
  return null;
}
