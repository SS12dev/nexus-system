import { Html, Line } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useMemo, useRef, useState } from "react";
import type { Group } from "three";
import { ORB_R, SAT_R, type Satellite, type Vec3 } from "./model";

const RING_STEPS = 96;
const TILT = 0.32;

/** The ring just outside the orb, and on it the routines (small rings, like a clock face) and
 * software projects (small cubes): what Nexus does and has made for you, as opposed to what it
 * knows, which is inside. Each has a faint tether to the shell; names show on hover. */
export function Satellites({
  items,
  still,
  selected,
  onPick,
}: {
  items: Satellite[];
  still: boolean;
  selected: string | null;
  onPick: (s: Satellite) => void;
}) {
  const group = useRef<Group>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const ring = useMemo(() => {
    const pts: Vec3[] = [];
    for (let i = 0; i <= RING_STEPS; i++) {
      const a = (i / RING_STEPS) * Math.PI * 2;
      const z = Math.sin(a) * SAT_R;
      pts.push([Math.cos(a) * SAT_R, z * Math.sin(TILT), z * Math.cos(TILT)]);
    }
    return pts;
  }, []);

  useFrame((_, dt) => {
    const g = group.current;
    if (!g) return;
    // The ring turns slowly the other way from the scene's own rotation.
    if (!still) g.rotation.y -= dt * 0.05;
    g.children.forEach((child) => {
      if (child.userData.spin && !still) child.rotation.y += dt * 0.9;
    });
  });

  if (items.length === 0) return null;
  return (
    <group ref={group}>
      <Line points={ring} color="#818cf8" transparent opacity={0.18} lineWidth={1} raycast={() => null} />
      {items.map((s) => {
        const shell: Vec3 = [
          (s.pos[0] / SAT_R) * ORB_R * 1.02,
          (s.pos[1] / SAT_R) * ORB_R * 1.02,
          (s.pos[2] / SAT_R) * ORB_R * 1.02,
        ];
        return (
          <Line
            key={`t-${s.id}`}
            points={[shell, s.pos]}
            color={s.color}
            transparent
            opacity={s.active ? 0.3 : 0.12}
            lineWidth={1}
            dashed={!s.active}
            dashSize={0.03}
            gapSize={0.03}
            raycast={() => null}
          />
        );
      })}
      {items.map((s) => {
        const on = s.id === selected || s.id === hovered;
        return (
          <group
            key={s.id}
            position={s.pos}
            userData={{ spin: true }}
            scale={on ? 1.5 : 1}
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
              <>
                <mesh rotation={[Math.PI / 2, 0, 0]}>
                  <torusGeometry args={[0.07, 0.014, 10, 28]} />
                  <meshStandardMaterial color={s.color} emissive={s.color} emissiveIntensity={s.active ? 1.4 : 0.3} toneMapped={false} />
                </mesh>
                <mesh>
                  <sphereGeometry args={[0.028, 12, 12]} />
                  <meshStandardMaterial color="#ffffff" emissive={s.color} emissiveIntensity={s.active ? 1.8 : 0.2} toneMapped={false} />
                </mesh>
              </>
            ) : (
              <mesh>
                <boxGeometry args={[0.085, 0.085, 0.085]} />
                <meshStandardMaterial
                  color={s.color}
                  emissive={s.color}
                  emissiveIntensity={s.active ? 1.3 : 0.25}
                  metalness={0.2}
                  roughness={0.3}
                  toneMapped={false}
                />
              </mesh>
            )}
            {/* A larger, invisible target so small satellites are easy to tap. */}
            <mesh visible={false}>
              <sphereGeometry args={[0.12, 8, 8]} />
              <meshBasicMaterial />
            </mesh>
            {on && (
              <Html center position={[0, 0.16, 0]} zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
                <div className="orb-label" style={{ borderColor: s.color }}>
                  {s.name}
                </div>
              </Html>
            )}
          </group>
        );
      })}
    </group>
  );
}
