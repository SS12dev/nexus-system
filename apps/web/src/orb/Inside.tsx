import { Html } from "@react-three/drei";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { AdditiveBlending, BufferAttribute, BufferGeometry, Color, Group, InstancedMesh, Mesh, Object3D } from "three";
import { ENTITY_COLOR, RECALL_COLOR } from "../lib/entities";
import { KIND_COLOR, type Crystal, type MemoryPoint, type StarLine, type StarPoint } from "./model";

const GROW_S = 0.9;

/** Memories as instanced particles inside the orb, clustered by kind. A memory Nexus just
 * learned grows in with a flash; pinned ones are larger and brighter. */
export function MemoryCloud({
  points,
  still,
  lite,
  selected,
  lit,
  onPick,
}: {
  points: MemoryPoint[];
  still: boolean;
  /** Phones draw fewer, larger particles, which are also easier to tap. */
  lite: boolean;
  selected: string | null;
  /** Memories the latest reply recalled, drawn larger in the recall colour. */
  lit: Set<string>;
  onPick: (p: MemoryPoint) => void;
}) {
  const mesh = useRef<InstancedMesh>(null);
  const dummy = useMemo(() => new Object3D(), []);
  const color = useMemo(() => new Color(), []);
  // When each id first appeared; ids present on the first render count as old.
  const born = useRef(new Map<string, number>());
  const first = useRef(true);
  const capacity = Math.max(points.length, 1);

  useEffect(() => {
    const now = performance.now();
    for (const p of points) if (!born.current.has(p.id)) born.current.set(p.id, first.current ? 0 : now);
    first.current = false;
  }, [points]);

  useLayoutEffect(() => {
    const m = mesh.current;
    if (!m) return;
    points.forEach((p, i) => {
      color.set(KIND_COLOR[p.kind] ?? "#cbd5e1").multiplyScalar(p.pinned ? 1.6 : 1);
      if (lit.has(p.id)) color.set(RECALL_COLOR).multiplyScalar(2);
      if (p.id === selected) color.set("#ffffff").multiplyScalar(2);
      m.setColorAt(i, color);
    });
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
  }, [points, selected, lit, color]);

  useFrame(({ clock }) => {
    const m = mesh.current;
    if (!m) return;
    const now = performance.now();
    const t = clock.elapsedTime;
    points.forEach((p, i) => {
      const age = (now - (born.current.get(p.id) ?? 0)) / 1000;
      const grow = still ? 1 : Math.min(1, age / GROW_S);
      // Overshoot slightly while growing so new memories read as a spark.
      const spark = grow < 1 ? 1 + Math.sin(grow * Math.PI) * 1.8 : 1;
      const base =
        (p.pinned ? 0.032 : 0.022) * (lite ? 1.5 : 1) * (p.id === selected ? 1.8 : lit.has(p.id) ? 1.6 : 1);
      const drift = still ? 0 : 0.012;
      dummy.position.set(
        p.pos[0] + Math.sin(t * 0.6 + i) * drift,
        p.pos[1] + Math.cos(t * 0.5 + i * 1.3) * drift,
        p.pos[2] + Math.sin(t * 0.4 + i * 0.7) * drift,
      );
      dummy.scale.setScalar(base * grow * spark);
      dummy.updateMatrix();
      m.setMatrixAt(i, dummy.matrix);
    });
    m.count = points.length;
    m.instanceMatrix.needsUpdate = true;
  });

  const click = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    if (e.instanceId != null && points[e.instanceId]) onPick(points[e.instanceId]);
  };

  return (
    // Re-create the mesh when the count outgrows it; instanced buffers have a fixed size.
    <instancedMesh
      key={capacity}
      ref={mesh}
      args={[undefined, undefined, capacity]}
      onClick={click}
      onPointerOver={() => (document.body.style.cursor = "pointer")}
      onPointerOut={() => (document.body.style.cursor = "")}
    >
      <sphereGeometry args={[1, 10, 10]} />
      <meshBasicMaterial toneMapped={false} />
    </instancedMesh>
  );
}

/** Artifacts (files Nexus or a team made) as small spinning crystals inside the orb. */
export function Crystals({
  items,
  still,
  selected,
  onPick,
}: {
  items: Crystal[];
  still: boolean;
  selected: string | null;
  onPick: (c: Crystal) => void;
}) {
  const group = useRef<Group>(null);
  const born = useRef(new Map<string, number>());
  const first = useRef(true);

  useEffect(() => {
    const now = performance.now();
    for (const c of items) if (!born.current.has(c.id)) born.current.set(c.id, first.current ? 0 : now);
    first.current = false;
  }, [items]);

  useFrame((_, dt) => {
    const g = group.current;
    if (!g) return;
    if (!still) g.rotation.y += dt * 0.12;
    const now = performance.now();
    g.children.forEach((child, i) => {
      const c = items[i];
      if (!c) return;
      if (!still) {
        child.rotation.x += dt * 0.8;
        child.rotation.y += dt * 1.1;
      }
      const age = (now - (born.current.get(c.id) ?? 0)) / 1000;
      const grow = still ? 1 : Math.min(1, age / GROW_S);
      child.scale.setScalar(grow * (c.id === selected ? 1.7 : 1));
    });
  });

  return (
    <group ref={group}>
      {items.map((c) => (
        <mesh
          key={c.id}
          position={c.pos}
          onClick={(e) => {
            e.stopPropagation();
            onPick(c);
          }}
          onPointerOver={() => (document.body.style.cursor = "pointer")}
          onPointerOut={() => (document.body.style.cursor = "")}
        >
          <octahedronGeometry args={[0.055, 0]} />
          <meshStandardMaterial
            color={c.id === selected ? "#ffffff" : "#e9d5ff"}
            emissive="#a855f7"
            emissiveIntensity={c.id === selected ? 2.4 : 1.2}
            metalness={0.3}
            roughness={0.15}
            flatShading
            toneMapped={false}
          />
        </mesh>
      ))}
    </group>
  );
}

const MAX_LINES = 256;

/** The knowledge graph inside the orb: you at the centre, the people, projects, places, topics
 * and tools Nexus knows as stars tinted by type, and lines for how they relate. What a reply
 * just recalled glows in the recall colour, with a pulse when it first lights up. */
export function Constellation({
  stars,
  lines,
  crystals,
  still,
  lite,
  selected,
  lit,
  litSeq,
  labels,
  onPick,
}: {
  stars: StarPoint[];
  lines: StarLine[];
  /** Artifacts with a project get a line to it. */
  crystals: Crystal[];
  still: boolean;
  lite: boolean;
  selected: string | null;
  lit: Set<string>;
  /** Changes each time a recall lights up, to replay the pulse. */
  litSeq: number;
  /** How many of the biggest stars get a name label; lit and selected ones always do. */
  labels: number;
  onPick: (s: StarPoint) => void;
}) {
  const pulseFrom = useRef(0);
  useEffect(() => {
    pulseFrom.current = performance.now();
  }, [litSeq]);
  const named = useMemo(
    () => new Set([...stars].filter((s) => !s.self).sort((a, b) => b.weight - a.weight).slice(0, labels).map((s) => s.id)),
    [stars, labels],
  );
  const heaviest = Math.max(1, ...stars.map((s) => s.weight));
  return (
    <group>
      <Lines stars={stars} lines={lines} crystals={crystals} lit={lit} />
      {stars.map((s) => (
        <Star
          key={s.id}
          star={s}
          size={s.self ? 0.05 : 0.026 + 0.026 * Math.sqrt(s.weight / heaviest)}
          lite={lite}
          still={still}
          selected={selected === s.id}
          lit={lit.has(s.id)}
          pulseFrom={pulseFrom}
          label={s.self ? "You" : named.has(s.id) || lit.has(s.id) || selected === s.id ? s.name : null}
          onPick={onPick}
        />
      ))}
    </group>
  );
}

function Star({
  star,
  size,
  lite,
  still,
  selected,
  lit,
  pulseFrom,
  label,
  onPick,
}: {
  star: StarPoint;
  size: number;
  lite: boolean;
  still: boolean;
  selected: boolean;
  lit: boolean;
  pulseFrom: { current: number };
  label: string | null;
  onPick: (s: StarPoint) => void;
}) {
  const halo = useRef<Mesh>(null);
  const color = star.self ? "#f8fafc" : lit ? RECALL_COLOR : (ENTITY_COLOR[star.type] ?? ENTITY_COLOR.other);
  // Phones get a bigger target to tap.
  const s = size * (lite ? 1.4 : 1) * (selected ? 1.5 : lit ? 1.35 : 1);
  useFrame(() => {
    const h = halo.current;
    if (!h) return;
    const t = (performance.now() - pulseFrom.current) / 1000;
    // Three quick beats when a recall lights it, then a steady glow.
    const beat = still || t > 1.8 ? 0 : Math.sin((t / 0.6) * Math.PI) ** 2;
    h.scale.setScalar(s * (1.7 + beat * 1.3));
  });
  return (
    <group position={star.pos}>
      <mesh
        scale={s}
        onClick={(e) => {
          e.stopPropagation();
          onPick(star);
        }}
        onPointerOver={() => (document.body.style.cursor = "pointer")}
        onPointerOut={() => (document.body.style.cursor = "")}
      >
        <sphereGeometry args={[1, lite ? 12 : 20, lite ? 8 : 14]} />
        <meshBasicMaterial color={color} toneMapped={false} />
      </mesh>
      {(lit || selected || star.self) && (
        <mesh ref={halo} raycast={() => null} scale={s * 1.7}>
          <sphereGeometry args={[1, 12, 8]} />
          <meshBasicMaterial
            color={selected ? "#ffffff" : color}
            transparent
            opacity={star.self ? 0.1 : 0.16}
            blending={AdditiveBlending}
            depthWrite={false}
            toneMapped={false}
          />
        </mesh>
      )}
      {label && (
        <Html center position={[0, s + 0.07, 0]} zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
          <div className={`orb-label orb-label-star${lit ? " orb-label-lit" : ""}`}>{label}</div>
        </Html>
      )}
    </group>
  );
}

/** Every relation, and each artifact's line to its project, in one draw call. */
function Lines({
  stars,
  lines,
  crystals,
  lit,
}: {
  stars: StarPoint[];
  lines: StarLine[];
  crystals: Crystal[];
  lit: Set<string>;
}) {
  const geometry = useMemo(() => {
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(new Float32Array(MAX_LINES * 6), 3));
    g.setAttribute("color", new BufferAttribute(new Float32Array(MAX_LINES * 6), 3));
    return g;
  }, []);
  useEffect(() => () => geometry.dispose(), [geometry]);

  useLayoutEffect(() => {
    const pos = geometry.getAttribute("position") as BufferAttribute;
    const col = geometry.getAttribute("color") as BufferAttribute;
    const at = new Map(stars.map((s) => [s.id, s.pos]));
    const c = new Color();
    let n = 0;
    const put = (a: number[], b: number[], hex: string, k: number) => {
      if (n >= MAX_LINES) return;
      c.set(hex).multiplyScalar(k);
      pos.setXYZ(n * 2, a[0], a[1], a[2]);
      pos.setXYZ(n * 2 + 1, b[0], b[1], b[2]);
      col.setXYZ(n * 2, c.r, c.g, c.b);
      col.setXYZ(n * 2 + 1, c.r, c.g, c.b);
      n++;
    };
    for (const l of lines) {
      const a = at.get(l.a);
      const b = at.get(l.b);
      if (!a || !b) continue;
      const hot = lit.has(l.a) && lit.has(l.b);
      // A recalled entity's line to you lights up too, so you can see the path.
      const toYou = (lit.has(l.a) || lit.has(l.b)) && stars.some((s) => s.self && (s.id === l.a || s.id === l.b));
      put(a, b, hot || toYou ? RECALL_COLOR : "#64748b", hot || toYou ? 0.9 : l.ended ? 0.25 : 0.55);
    }
    for (const cr of crystals) {
      const a = cr.star ? at.get(cr.star) : undefined;
      if (a) put(a, cr.pos, "#a855f7", 0.45);
    }
    geometry.setDrawRange(0, n * 2);
    pos.needsUpdate = true;
    col.needsUpdate = true;
  }, [geometry, stars, lines, crystals, lit]);

  return (
    <lineSegments geometry={geometry} raycast={() => null} frustumCulled={false}>
      <lineBasicMaterial vertexColors transparent opacity={0.8} toneMapped={false} />
    </lineSegments>
  );
}
