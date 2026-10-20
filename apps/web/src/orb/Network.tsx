import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Color,
  Group,
  Mesh,
  Vector3,
} from "three";
import { toolLabel } from "../lib/tools";
import { hash, isActive, NODE_COLOR, ORB_R, type OrbCluster, type OrbNode } from "./model";

interface Entry {
  node: OrbNode;
  /** Shown above the lead: the team's name. */
  label: string | null;
  /** Gone from the data; shrinking back into its parent, then removed. */
  leaving: boolean;
  /** Seconds to wait before budding off the parent, so a team unfolds lead first. */
  delay: number;
}

type Positions = Map<string, { pos: Vector3; grow: number; status: string }>;

const SPRING = 16;
const DAMP = 2 * Math.sqrt(SPRING) * 0.75; // a little under critical, so arrivals settle softly
const MAX_EDGES = 256;

/** The live agent network around the orb. Teams grow out of the orb when they are created
 * and pull back into it when they are deleted or archived; agents move on springs, never jump. */
export function Network({
  clusters,
  still,
  lite,
  selected,
  onPick,
}: {
  clusters: OrbCluster[];
  still: boolean;
  lite: boolean;
  selected: string | null;
  onPick: (node: OrbNode) => void;
}) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const positions = useRef<Positions>(new Map());

  useEffect(() => {
    setEntries((prev) => {
      const next: Entry[] = [];
      const current = new Set<string>();
      for (const c of clusters) {
        c.nodes.forEach((n, j) => {
          current.add(n.key);
          next.push({ node: n, label: n.lead ? c.name : null, leaving: false, delay: j === 0 ? 0 : 0.25 + j * 0.06 });
        });
      }
      for (const e of prev) if (!current.has(e.node.key)) next.push({ ...e, leaving: true, label: null });
      return next;
    });
  }, [clusters]);

  const gone = (key: string) => {
    positions.current.delete(key);
    setEntries((prev) => prev.filter((e) => !(e.leaving && e.node.key === key)));
  };

  return (
    <group>
      <Edges entries={entries} positions={positions.current} />
      {entries.map((e) => (
        <AgentNode
          key={e.node.key}
          entry={e}
          positions={positions.current}
          still={still}
          lite={lite}
          selected={selected === e.node.key}
          onPick={onPick}
          onGone={gone}
        />
      ))}
    </group>
  );
}

function AgentNode({
  entry,
  positions,
  still,
  lite,
  selected,
  onPick,
  onGone,
}: {
  entry: Entry;
  positions: Positions;
  still: boolean;
  lite: boolean;
  selected: boolean;
  onPick: (node: OrbNode) => void;
  onGone: (key: string) => void;
}) {
  const { node, leaving, label, delay } = entry;
  const group = useRef<Group>(null);
  const halo = useRef<Mesh>(null);
  const pulse = useRef<Mesh>(null);
  const state = useRef<{ pos: Vector3; vel: Vector3; grow: number; wait: number } | null>(null);
  const target = useMemo(() => new Vector3(), []);
  const from = useMemo(() => new Vector3(), []);
  const phase = useMemo(() => (hash(node.key) % 1000) / 1000, [node.key]);
  const color = NODE_COLOR[node.status] ?? NODE_COLOR.idle;
  const active = isActive(node.status);
  const size = node.lead ? 0.16 : 0.11;

  useFrame(({ clock }, rawDt) => {
    const dt = Math.min(rawDt, 0.05);
    const parent = node.parent ? positions.get(node.parent) : undefined;
    if (!state.current) {
      // Start inside the parent (or just inside the orb) so the agent grows out of it.
      const start = parent
        ? parent.pos.clone()
        : new Vector3(...node.target).normalize().multiplyScalar(ORB_R * 0.4);
      if (still) start.set(...node.target);
      state.current = { pos: start, vel: new Vector3(), grow: still ? 1 : 0, wait: still ? 0 : delay };
    }
    const s = state.current;
    if (leaving) {
      // Pull back toward where it came from, shrinking as it goes.
      if (parent && parent.grow > 0.05) target.copy(parent.pos);
      else target.set(...node.target).normalize().multiplyScalar(ORB_R * 0.3);
      s.grow -= dt * (still ? 100 : 1.4);
      if (s.grow <= 0) {
        onGone(node.key);
        return;
      }
    } else if (s.wait > 0) {
      s.wait -= dt;
      if (parent) s.pos.copy(parent.pos);
    } else {
      target.set(...node.target);
      if (!still) target.y += Math.sin(clock.elapsedTime * 0.7 + phase * 6.28) * 0.05;
      s.grow = Math.min(1, s.grow + dt * 1.6);
    }
    if (still && !leaving) {
      s.pos.copy(target);
    } else if (s.wait <= 0 || leaving) {
      const ax = SPRING * (target.x - s.pos.x) - DAMP * s.vel.x;
      const ay = SPRING * (target.y - s.pos.y) - DAMP * s.vel.y;
      const az = SPRING * (target.z - s.pos.z) - DAMP * s.vel.z;
      s.vel.x += ax * dt;
      s.vel.y += ay * dt;
      s.vel.z += az * dt;
      s.pos.addScaledVector(s.vel, dt);
    }
    positions.set(node.key, { pos: s.pos, grow: s.grow, status: node.status });

    const g = group.current;
    if (g) {
      g.position.copy(s.pos);
      g.scale.setScalar(Math.max(s.grow, 0.0001));
    }
    if (halo.current) {
      const beat = still ? 0.5 : (Math.sin(clock.elapsedTime * 4 + phase * 6.28) + 1) / 2;
      halo.current.scale.setScalar(size * (1.3 + beat * 0.35));
    }
    if (pulse.current) {
      // A spark runs down the edge from the parent (or the orb) toward a busy agent.
      if (parent) from.copy(parent.pos);
      else from.copy(s.pos).normalize().multiplyScalar(ORB_R);
      const t = still ? 0.6 : (clock.elapsedTime * 0.9 + phase) % 1;
      pulse.current.position.lerpVectors(from, s.pos, t);
      pulse.current.visible = s.grow > 0.5;
    }
  });

  return (
    <>
      <group ref={group}>
        <mesh
          scale={size}
          onClick={(e) => {
            e.stopPropagation();
            if (!leaving) onPick(node);
          }}
          onPointerOver={() => (document.body.style.cursor = "pointer")}
          onPointerOut={() => (document.body.style.cursor = "")}
        >
          <sphereGeometry args={[1, lite ? 16 : 28, lite ? 12 : 20]} />
          <meshStandardMaterial
            color={color}
            emissive={color}
            emissiveIntensity={active ? 1.4 : selected ? 1 : 0.45}
            roughness={0.35}
            metalness={0.1}
            toneMapped={false}
          />
        </mesh>
        {(active || selected) && (
          <mesh ref={halo} raycast={() => null} scale={size * 1.4}>
            <sphereGeometry args={[1, 16, 12]} />
            <meshBasicMaterial
              color={selected ? "#ffffff" : color}
              transparent
              opacity={selected ? 0.22 : 0.12}
              blending={AdditiveBlending}
              depthWrite={false}
              toneMapped={false}
            />
          </mesh>
        )}
        {label && (
          <Html center position={[0, size + 0.22, 0]} zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
            <div className="orb-label">{label}</div>
          </Html>
        )}
        {node.tool && ["working", "waiting"].includes(node.status) && !leaving && (
          <Html center position={[0, -size - 0.16, 0]} zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
            <div className="orb-label orb-label-tool" style={{ borderColor: color }}>
              {toolLabel(node.tool)}
            </div>
          </Html>
        )}
      </group>
      {active && !leaving && (
        <mesh ref={pulse} raycast={() => null} scale={0.035}>
          <sphereGeometry args={[1, 8, 6]} />
          <meshBasicMaterial color="#ffffff" toneMapped={false} />
        </mesh>
      )}
    </>
  );
}

/** Every edge in one draw call: orb to each team's lead, and each agent to its parent.
 * Rebuilt each frame from the springs' current positions. */
function Edges({ entries, positions }: { entries: Entry[]; positions: Positions }) {
  const geometry = useMemo(() => {
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(new Float32Array(MAX_EDGES * 6), 3));
    g.setAttribute("color", new BufferAttribute(new Float32Array(MAX_EDGES * 6), 3));
    g.setDrawRange(0, 0);
    return g;
  }, []);
  const c = useMemo(() => new Color(), []);
  const dir = useMemo(() => new Vector3(), []);
  useEffect(() => () => geometry.dispose(), [geometry]);

  useFrame(() => {
    const pos = geometry.getAttribute("position") as BufferAttribute;
    const col = geometry.getAttribute("color") as BufferAttribute;
    let n = 0;
    for (const e of entries) {
      if (n >= MAX_EDGES) break;
      const me = positions.get(e.node.key);
      if (!me || me.grow <= 0.01) continue;
      let a: Vector3;
      if (e.node.parent) {
        const p = positions.get(e.node.parent);
        if (!p) continue;
        a = p.pos;
      } else {
        a = dir.copy(me.pos).normalize().multiplyScalar(ORB_R * 1.02);
      }
      const b = me.pos;
      pos.setXYZ(n * 2, a.x, a.y, a.z);
      pos.setXYZ(n * 2 + 1, b.x, b.y, b.z);
      c.set(isActive(me.status) ? NODE_COLOR[me.status] : "#334155").multiplyScalar(
        Math.min(1, me.grow) * (isActive(me.status) ? 1 : 0.9),
      );
      col.setXYZ(n * 2, c.r * 0.6, c.g * 0.6, c.b * 0.6);
      col.setXYZ(n * 2 + 1, c.r, c.g, c.b);
      n++;
    }
    geometry.setDrawRange(0, n * 2);
    pos.needsUpdate = true;
    col.needsUpdate = true;
  });

  return (
    <lineSegments geometry={geometry} raycast={() => null} frustumCulled={false}>
      <lineBasicMaterial vertexColors transparent opacity={0.85} toneMapped={false} />
    </lineSegments>
  );
}
