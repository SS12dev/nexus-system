import { Line } from "@react-three/drei";
import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AdditiveBlending, Color, InstancedMesh, Mesh, Object3D, Vector3 } from "three";
import { ENTITY_COLOR, RECALL_COLOR } from "../lib/entities";
import {
  curve,
  hash,
  KIND_COLOR,
  type MemoryPoint,
  type StarLine,
  type StarPoint,
  type Vec3,
} from "./model";

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

/** The knowledge graph inside the orb: you at the centre, the people, projects, places, topics
 * and tools Nexus knows as stars tinted by type, and lines for how they relate. What a reply
 * just recalled glows in the recall colour, with a pulse when it first lights up. Names are
 * plain page elements in `labels` (see StarLabels), placed over the stars every frame. */
export function Constellation({
  stars,
  lines,
  still,
  lite,
  selected,
  lit,
  litSeq,
  labels,
  tags,
  onPick,
}: {
  stars: StarPoint[];
  lines: StarLine[];
  still: boolean;
  lite: boolean;
  selected: string | null;
  lit: Set<string>;
  /** Changes each time a recall lights up, to replay the pulse. */
  litSeq: number;
  /** How many names show from the home view; more show as you zoom in. */
  labels: number;
  /** Each star's name element, by star id. */
  tags: Map<string, HTMLElement>;
  onPick: (s: StarPoint) => void;
}) {
  const pulseFrom = useRef(0);
  const [hovered, setHovered] = useState<string | null>(null);
  useEffect(() => {
    pulseFrom.current = performance.now();
  }, [litSeq]);
  const heaviest = Math.max(1, ...stars.map((s) => s.weight));
  const sizeOf = (s: StarPoint) =>
    (s.self ? 0.05 : 0.026 + 0.026 * Math.sqrt(s.weight / heaviest)) *
    (lite ? 1.4 : 1) *
    (selected === s.id ? 1.5 : lit.has(s.id) ? 1.35 : 1);
  // Names in the order they win a spot when two would overlap.
  const order = useMemo(() => {
    const rank = (s: StarPoint) =>
      s.id === selected || s.id === hovered ? 4 : lit.has(s.id) ? 3 : s.self ? 2 : 1 + s.weight / (heaviest + 1);
    return [...stars].sort((a, b) => rank(b) - rank(a));
  }, [stars, selected, hovered, lit, heaviest]);
  const always = (st: StarPoint) => st.self || st.id === selected || st.id === hovered || lit.has(st.id);
  usePlaceLabels(order, tags, labels, always, sizeOf);
  return (
    <group>
      <Lines stars={stars} lines={lines} lit={lit} />
      {stars.map((s) => (
        <Star
          key={s.id}
          star={s}
          size={sizeOf(s)}
          lite={lite}
          still={still}
          selected={selected === s.id}
          lit={lit.has(s.id)}
          pulseFrom={pulseFrom}
          onHover={(on) => setHovered((h) => (on ? s.id : h === s.id ? null : h))}
          onPick={onPick}
        />
      ))}
    </group>
  );
}

/** Put each name just above its star, every frame so they follow the orb as it turns. Which
 * names show is decided a few times a second: the most important first, up to `labels` from
 * the home view and more as you zoom in, never one that would cover another. `always` ones
 * (you, and what is selected, hovered or just recalled) don't count towards the limit. */
function usePlaceLabels(
  order: StarPoint[],
  tags: Map<string, HTMLElement>,
  labels: number,
  always: (s: StarPoint) => boolean,
  sizeOf: (s: StarPoint) => number,
) {
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  const v = useMemo(() => new Vector3(), []);
  const tick = useRef(0);
  useFrame(() => {
    const decide = tick.current++ % 6 === 0;
    const zoom = Math.min(6, Math.max(1, (4 / camera.position.length()) ** 2));
    let room = Math.round(labels * zoom);
    const taken: [number, number, number, number][] = [];
    for (const s of order) {
      const el = tags.get(s.id);
      if (!el) continue;
      v.set(s.pos[0], s.pos[1] + sizeOf(s) + 0.02, s.pos[2]).project(camera);
      const x = ((v.x + 1) / 2) * size.width;
      const y = ((1 - v.y) / 2) * size.height;
      el.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) translate(-50%, -100%)`;
      if (!decide) continue;
      const must = always(s);
      if (v.z > 1 || (!must && room <= 0)) {
        el.style.opacity = "0";
        continue;
      }
      const w = el.offsetWidth / 2 + 2;
      const h = el.offsetHeight;
      const box: [number, number, number, number] = [x - w, y - h - 1, x + w, y + 1];
      const clash = taken.some((t) => box[0] < t[2] && box[2] > t[0] && box[1] < t[3] && box[3] > t[1]);
      el.style.opacity = clash ? "0" : "1";
      if (clash) continue;
      taken.push(box);
      if (!must) room--;
    }
  });
}

function Star({
  star,
  size: s,
  lite,
  still,
  selected,
  lit,
  pulseFrom,
  onHover,
  onPick,
}: {
  star: StarPoint;
  size: number;
  lite: boolean;
  still: boolean;
  selected: boolean;
  lit: boolean;
  pulseFrom: { current: number };
  onHover: (on: boolean) => void;
  onPick: (s: StarPoint) => void;
}) {
  const halo = useRef<Mesh>(null);
  const color = star.self ? "#f8fafc" : lit ? RECALL_COLOR : (ENTITY_COLOR[star.type] ?? ENTITY_COLOR.other);
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
        onPointerOver={(e) => {
          e.stopPropagation();
          document.body.style.cursor = "pointer";
          onHover(true);
        }}
        onPointerOut={() => {
          document.body.style.cursor = "";
          onHover(false);
        }}
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
    </group>
  );
}

const LINE = { open: "#cbd5e1", ended: "#64748b" };

/** Every relation as screen-space lines a couple of pixels wide, so they stay readable at any
 * zoom and over the glow of the shell. */
function Lines({ stars, lines, lit }: { stars: StarPoint[]; lines: StarLine[]; lit: Set<string> }) {
  const { normal, hot } = useMemo(() => {
    const at = new Map(stars.map((s) => [s.id, s.pos]));
    const self = stars.find((s) => s.self)?.id;
    const normal = { points: [] as Vec3[], colors: [] as Vec3[] };
    const hot = { points: [] as Vec3[], colors: [] as Vec3[] };
    const c = new Color();
    // Relations bow gently, each its own way, like threads rather than spokes.
    const put = (into: typeof normal, a: Vec3, b: Vec3, hex: string, k: number, seed = 0) => {
      c.set(hex).multiplyScalar(k);
      const pts = seed ? curve(a, b, seed, 0.14, 8) : [a, b];
      for (let i = 0; i < pts.length - 1; i++) {
        into.points.push(pts[i], pts[i + 1]);
        into.colors.push([c.r, c.g, c.b], [c.r, c.g, c.b]);
      }
    };
    for (const l of lines) {
      const a = at.get(l.a);
      const b = at.get(l.b);
      if (!a || !b) continue;
      // A recalled entity's line to you lights up too, so you can see the path.
      const lights =
        (lit.has(l.a) && lit.has(l.b)) || ((lit.has(l.a) || lit.has(l.b)) && (l.a === self || l.b === self));
      const seed = hash(`${l.a}:${l.b}`);
      if (lights) put(hot, a, b, RECALL_COLOR, 1, seed);
      else put(normal, a, b, l.ended ? LINE.ended : LINE.open, l.ended ? 0.6 : 1, seed);
    }
    return { normal, hot };
  }, [stars, lines, lit]);

  return (
    <>
      {normal.points.length > 0 && (
        <Line
          segments
          points={normal.points}
          vertexColors={normal.colors}
          lineWidth={1.4}
          transparent
          opacity={0.7}
          depthWrite={false}
          toneMapped={false}
          raycast={() => null}
        />
      )}
      {hot.points.length > 0 && (
        <Line
          segments
          points={hot.points}
          vertexColors={hot.colors}
          lineWidth={2.4}
          transparent
          opacity={0.95}
          depthWrite={false}
          toneMapped={false}
          raycast={() => null}
        />
      )}
    </>
  );
}
