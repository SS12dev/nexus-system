import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { Color, Group, InstancedMesh, Object3D } from "three";
import { KIND_COLOR, type Crystal, type MemoryPoint } from "./model";

const GROW_S = 0.9;

/** Memories as instanced particles inside the orb, clustered by kind. A memory Jarvis just
 * learned grows in with a flash; pinned ones are larger and brighter. */
export function MemoryCloud({
  points,
  still,
  lite,
  selected,
  onPick,
}: {
  points: MemoryPoint[];
  still: boolean;
  /** Phones draw fewer, larger particles, which are also easier to tap. */
  lite: boolean;
  selected: string | null;
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
      if (p.id === selected) color.set("#ffffff").multiplyScalar(2);
      m.setColorAt(i, color);
    });
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
  }, [points, selected, color]);

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
      const base = (p.pinned ? 0.032 : 0.022) * (lite ? 1.5 : 1) * (p.id === selected ? 1.8 : 1);
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

/** Artifacts (files Jarvis or a team made) as small spinning crystals inside the orb. */
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
