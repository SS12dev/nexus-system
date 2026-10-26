import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useMemo, useRef, useState } from "react";
import { AdditiveBlending, BufferAttribute, BufferGeometry, Color, type Group } from "three";
import { curve, hash, orbitPos, type Satellite, type StarPoint } from "./model";

const THREAD_STEPS = 12;

/** Routines and projects as small moons outside the orb, each drifting on an orbit of its own,
 * with a faint curved thread back to what it is about inside (a routine's topic or you; a
 * project's topic, when it has one). Routines are soft violet moons, apps small amber
 * crystals, and a project's files one violet crystal that grows with how many it holds, in
 * place of a crystal per file; names show on hover. */
export function Satellites({
  items,
  stars,
  still,
  selected,
  onPick,
}: {
  items: Satellite[];
  stars: StarPoint[];
  still: boolean;
  selected: string | null;
  onPick: (s: Satellite) => void;
}) {
  const moons = useRef<(Group | null)[]>([]);
  const [hovered, setHovered] = useState<string | null>(null);
  const clock = useRef(0);
  const at = useMemo(() => new Map(stars.map((s) => [s.id, s.pos])), [stars]);

  // One line geometry for every thread, rewritten each frame as the moons move.
  const threads = useMemo(() => {
    const g = new BufferGeometry();
    const n = items.length * THREAD_STEPS * 2;
    g.setAttribute("position", new BufferAttribute(new Float32Array(n * 3), 3));
    const colors = new Float32Array(n * 3);
    const c = new Color();
    items.forEach((s, i) => {
      c.set(s.color).multiplyScalar(s.active ? 0.55 : 0.25);
      for (let k = 0; k < THREAD_STEPS * 2; k++) colors.set([c.r, c.g, c.b], (i * THREAD_STEPS * 2 + k) * 3);
    });
    g.setAttribute("color", new BufferAttribute(colors, 3));
    return g;
  }, [items]);

  useFrame((_, dt) => {
    if (!still) clock.current += dt;
    const pos = threads.getAttribute("position") as BufferAttribute;
    items.forEach((s, i) => {
      const p = orbitPos(s.orbit, clock.current);
      const moon = moons.current[i];
      if (moon) {
        moon.position.set(p[0], p[1], p[2]);
        const on = s.id === selected || s.id === hovered;
        moon.scale.setScalar(on ? 1.6 : 1);
        if (!still) moon.rotation.y += dt * 0.6;
      }
      const home = s.home ? at.get(s.home) : undefined;
      // No home, no thread: its segments collapse onto the moon.
      const pts = home ? curve(p, home, hash(s.id), 0.22, THREAD_STEPS) : Array(THREAD_STEPS + 1).fill(p);
      for (let k = 0; k < THREAD_STEPS; k++) {
        const o = (i * THREAD_STEPS + k) * 2;
        pos.setXYZ(o, pts[k][0], pts[k][1], pts[k][2]);
        pos.setXYZ(o + 1, pts[k + 1][0], pts[k + 1][1], pts[k + 1][2]);
      }
    });
    pos.needsUpdate = true;
  });

  if (items.length === 0) return null;
  return (
    <group>
      <lineSegments geometry={threads} raycast={() => null} frustumCulled={false}>
        <lineBasicMaterial vertexColors transparent opacity={0.5} depthWrite={false} blending={AdditiveBlending} toneMapped={false} />
      </lineSegments>
      {items.map((s, i) => {
        const on = s.id === selected || s.id === hovered;
        return (
          <group
            key={s.id}
            ref={(g) => {
              moons.current[i] = g;
            }}
            position={s.pos}
            onClick={(e) => {
              e.stopPropagation();
              onPick(s);
            }}
            onPointerOver={(e) => {
              e.stopPropagation();
              setHovered(s.id);
              document.body.style.cursor = "pointer";
            }}
            onPointerOut={() => {
              setHovered((h) => (h === s.id ? null : h));
              document.body.style.cursor = "";
            }}
          >
            {s.kind === "routine" ? (
              <mesh>
                <sphereGeometry args={[0.045, 20, 20]} />
                <meshStandardMaterial color={s.color} emissive={s.color} emissiveIntensity={s.active ? 0.75 : 0.2} roughness={0.7} />
              </mesh>
            ) : !s.app ? (
              <mesh>
                <octahedronGeometry args={[0.05 * (1 + Math.min(s.files, 12) * 0.05), 0]} />
                <meshStandardMaterial
                  color="#e9d5ff"
                  emissive={s.color}
                  emissiveIntensity={on ? 2 : 1.1}
                  metalness={0.3}
                  roughness={0.15}
                  flatShading
                  toneMapped={false}
                />
              </mesh>
            ) : (
              <mesh>
                <icosahedronGeometry args={[0.05, 0]} />
                <meshStandardMaterial
                  color={s.color}
                  emissive={s.color}
                  emissiveIntensity={s.active ? 0.8 : 0.2}
                  roughness={0.3}
                  flatShading
                />
              </mesh>
            )}
            {/* A larger, invisible target so small moons are easy to tap. */}
            <mesh visible={false}>
              <sphereGeometry args={[0.13, 8, 8]} />
              <meshBasicMaterial />
            </mesh>
            {on && (
              <Html center position={[0, 0.17, 0]} zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
                <div className="orb-label" style={{ borderColor: s.color }}>
                  {s.name}
                  {s.kind === "project" && s.files > 0 && (
                    <span className="text-slate-400">
                      {" "}
                      · {s.files} file{s.files === 1 ? "" : "s"}
                    </span>
                  )}
                </div>
              </Html>
            )}
          </group>
        );
      })}
    </group>
  );
}
