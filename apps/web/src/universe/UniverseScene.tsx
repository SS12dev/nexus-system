import { CameraControls, Html } from "@react-three/drei";
import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { Bloom, EffectComposer, Vignette } from "@react-three/postprocessing";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { AssistantState, Universe, UniverseArea, UniverseStar } from "../lib/types";
import type { OrbCluster, OrbNode } from "./teams";
import type { Perf } from "./perf";
import * as kit from "./kit";
import {
  applyEuler,
  areaLine,
  CORE_R,
  crewLight,
  DEFAULT_EYE,
  crewRole,
  deckActivity,
  HUB_POS,
  HUB_SCALE,
  placeGalaxies,
  shipType,
  starHue,
  starLook,
  starWorld,
  stateColor,
  teamTarget,
  tierScale,
  visibleStars,
  type GalaxyLayout,
  type Vec3,
} from "./model";

export type Selection =
  | { type: "star"; star: UniverseStar }
  | { type: "area"; area: UniverseArea }
  | { type: "hub" }
  | { type: "core" }
  | { type: "team"; cluster: OrbCluster }
  | { type: "agent"; node: OrbNode };

type Pick =
  | { type: "star"; id: string }
  | { type: "area"; id: string }
  | { type: "hub" }
  | { type: "core" }
  | { type: "team"; id: string };

interface Props {
  universe: Universe | null;
  clusters: OrbCluster[];
  assistantState: AssistantState;
  /** What the latest reply recalled, growing hop by hop; litSeq changes each time. */
  lit: Set<string>;
  litSeq: number;
  /** Bumps when a memory is saved: a spark flies from the hub into the core. */
  memorySeq: number;
  perf: Perf;
  admin: boolean;
  /** No names anywhere: for screen sharing. */
  privateMode: boolean;
  /** Smaller, beside a chat. */
  compact: boolean;
  selected: Selection | null;
  onSelect: (s: Selection | null) => void;
}

const BG = "#02030a";

/** The universe: your core at the centre, the Nexus hub beside it, a galaxy per life area, and
 * a ship per team at work. Lazy-loaded, so three.js only downloads with the view. */
export default function UniverseScene(props: Props) {
  const { perf, compact } = props;
  const still = perf.reducedMotion;
  return (
    <Canvas
      dpr={perf.lite ? [1, 1.25] : [1, 1.75]}
      camera={{ position: DEFAULT_EYE, fov: compact ? 50 : 42, near: 0.1, far: 2500 }}
      gl={{ antialias: !perf.lite, powerPreference: perf.lite ? "low-power" : "high-performance" }}
      onCreated={({ gl }) => {
        gl.toneMapping = THREE.ACESFilmicToneMapping;
        gl.toneMappingExposure = 1.05;
      }}
      frameloop={still ? "demand" : "always"}
      onPointerMissed={() => props.onSelect(null)}
    >
      <color attach="background" args={[BG]} />
      <World {...props} />
      {!perf.lite && (
        <EffectComposer multisampling={0}>
          <Bloom intensity={0.55} luminanceThreshold={0.42} luminanceSmoothing={0.35} mipmapBlur />
          <Vignette offset={0.25} darkness={0.55} />
        </EffectComposer>
      )}
    </Canvas>
  );
}

interface Built {
  root: THREE.Group;
  picks: THREE.Group;
  ticks: kit.Tick[];
  galaxies: Map<string, { layout: GalaxyLayout; material: THREE.ShaderMaterial }>;
  stars: Map<string, { obj: THREE.Object3D; halo?: THREE.Sprite; world: Vec3; base: number }>;
  roots: Map<string, { line: THREE.Mesh; halo: THREE.Mesh; curve: THREE.Curve<THREE.Vector3> }>;
  hub: ReturnType<typeof kit.hub>;
  coreShimmer: THREE.ShaderMaterial;
  starsById: Map<string, UniverseStar>;
  areasById: Map<string, UniverseArea>;
  coreIds: Set<string>;
  starWorldPos: Map<string, Vec3>;
}

function pickSphere(radius: number, pick: Pick, pos: Vec3) {
  const m = new THREE.Mesh(
    new THREE.SphereGeometry(radius, 12, 8),
    new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false, colorWrite: false }),
  );
  m.position.set(...pos);
  m.userData.pick = pick;
  return m;
}

function build(u: Universe | null, perf: Perf, admin: boolean): Built {
  const root = new THREE.Group();
  const picks = new THREE.Group();
  const ticks: kit.Tick[] = [];
  const rng = kit.seeded(7);
  root.add(kit.starfield(perf.lite ? 1500 : 4500, 900, rng));
  (
    [
      [0x3a2a80, [-90, 30, -180], 260],
      [0x0f4a7a, [110, -40, -200], 280],
      [0x5a1f5a, [10, 70, -240], 220],
      [0x1d3a7a, [-160, -20, 60], 240],
    ] as const
  ).forEach(([c, p, s], i) => root.add(kit.nebula(c, new THREE.Vector3(...p), s, 0.32, i * 1.7)));
  kit.lights(root);

  // The core: who you are, never named.
  const neurons = u?.core.neurons ?? [];
  const core = kit.core(CORE_R, neurons, u?.core.links ?? [], u?.core.hue ?? 0.58, ticks);
  root.add(core.group);
  picks.add(pickSphere(CORE_R, { type: "core" }, [0, 0, 0]));

  // The hub: Nexus, which is Orion; tethered to the core by memory.
  const hub = kit.hub(admin, ticks);
  hub.group.position.set(...HUB_POS);
  hub.group.scale.setScalar(HUB_SCALE);
  hub.group.rotation.z = 0.08;
  root.add(hub.group);
  picks.add(pickSphere(4, { type: "hub" }, HUB_POS));
  const tether = kit.tube(
    [new THREE.Vector3(4.6, 0.8, 1.6), new THREE.Vector3(7, 1.8, 3.4), new THREE.Vector3(HUB_POS[0] - 0.6, HUB_POS[1] - 0.1, HUB_POS[2])],
    0x7cc4ff,
    0.05,
    0.9,
  );
  root.add(tether.group);
  if (!perf.reducedMotion) root.add(kit.flowDots(tether.curve, 0xbfe6ff, 4, 0.18, 0.5, ticks));

  // Galaxies, one per life area.
  const areas = u?.areas ?? [];
  const layouts = placeGalaxies(areas, perf.lite ? 9000 : 32000);
  const galaxies = new Map<string, { layout: GalaxyLayout; material: THREE.ShaderMaterial }>();
  const roots = new Map<string, { line: THREE.Mesh; halo: THREE.Mesh; curve: THREE.Curve<THREE.Vector3> }>();
  const rootWeight = new Map(u?.roots.map((r) => [r.area, r.w]) ?? []);
  for (const g of layouts) {
    const a = g.area;
    const gx = kit.galaxy({
      radius: g.radius,
      arms: 2 + (kit.hashOf(a.id) % 3),
      n: g.particles,
      hue: a.hue,
      hue2: (a.hue + 0.12) % 1,
      seed: kit.hashOf(a.id),
      newborn: g.newborn,
    });
    gx.group.position.set(...g.center);
    gx.group.rotation.set(...g.rot);
    if (g.ending) gx.material.uniforms.uDim.value = 0.3;
    root.add(gx.group);
    galaxies.set(a.id, { layout: g, material: gx.material });
    // An area that merged away or emptied still answers a tap while it fades, to say where it went.
    picks.add(pickSphere(g.radius * 0.9, { type: "area", id: a.id }, g.center));
    // One soft root from the core to each galaxy, brighter the more of you is in it.
    const c = new THREE.Vector3(...g.center);
    const from = c.clone().normalize().multiplyScalar(CORE_R * 1.02);
    const mid = from.clone().lerp(c, 0.5).add(new THREE.Vector3(0, 3, 0));
    const w = rootWeight.get(a.id) ?? 0;
    const color = new THREE.Color().setHSL(a.hue, 0.7, 0.6);
    const tb = kit.tube([from, mid, c], color, 0.025 + Math.min(0.04, w * 0.015), g.ending ? 0.03 : 0.08 + Math.min(0.14, w * 0.05));
    tb.line.userData.base = (tb.line.material as THREE.MeshBasicMaterial).opacity;
    root.add(tb.group);
    roots.set(a.id, { line: tb.line, halo: tb.halo, curve: tb.curve });
  }
  // Filaments between areas that link.
  for (const f of u?.filaments ?? []) {
    const A = galaxies.get(f.a)?.layout;
    const B = galaxies.get(f.b)?.layout;
    if (!A || !B) continue;
    const a = new THREE.Vector3(...A.center);
    const b = new THREE.Vector3(...B.center);
    const mid = a.clone().lerp(b, 0.5).multiplyScalar(1.12).add(new THREE.Vector3(0, 2, 0));
    const tb = kit.tube([a, mid, b], 0x8fb4ff, 0.025 + Math.min(0.06, f.w * 0.02), 0.22);
    root.add(tb.group);
    if (!perf.reducedMotion && !perf.lite) root.add(kit.flowDots(tb.curve, 0xcfe4ff, 2, 0.04, 0.6, ticks));
  }

  // Stars and structures in each galaxy.
  const starsById = new Map((u?.stars ?? []).map((s) => [s.id, s]));
  const areasById = new Map(areas.map((a) => [a.id, a]));
  const stars = new Map<string, { obj: THREE.Object3D; halo?: THREE.Sprite; world: Vec3; base: number }>();
  const starWorldPos = new Map<string, Vec3>();
  for (const s of u?.stars ?? []) {
    const g = s.area ? galaxies.get(s.area)?.layout : undefined;
    if (g && s.pos) starWorldPos.set(s.id, starWorld(g, s.pos));
  }
  for (const s of visibleStars(u?.stars ?? [], perf.lite ? 8 : 16)) {
    const g = galaxies.get(s.area!)!.layout;
    const world = starWorldPos.get(s.id)!;
    const look = starLook(s);
    const hue = starHue(s, g.area.hue);
    const color = new THREE.Color().setHSL(hue, 0.75, 0.68);
    const size = 0.45 + 1.1 * Math.sqrt(Math.max(0.02, s.salience));
    let obj: THREE.Object3D;
    let halo: THREE.Sprite | undefined;
    if (look === "pulsar") {
      obj = kit.pulsar(color, ticks, 3 + (kit.hashOf(s.id) % 5));
      obj.scale.setScalar(0.45);
    } else if (look === "outpost") {
      obj = kit.outpost(true).group;
      obj.scale.setScalar(0.5);
    } else if (look === "site") {
      obj = kit.constructionSite(false);
      obj.scale.setScalar(0.5);
    } else if (look === "dust") {
      const grp = new THREE.Group();
      halo = kit.sprite(color, size * 1.6, 0.35, kit.tex().soft);
      grp.add(halo);
      obj = grp;
    } else {
      const grp = new THREE.Group();
      grp.add(new THREE.Mesh(new THREE.SphereGeometry(0.12 * size, 12, 8), kit.glowMat(color, 4)));
      halo = kit.sprite(color, 1.6 * size, 0.7);
      grp.add(halo);
      grp.add(kit.sprite(color, 3 * size, 0.18, kit.tex().soft));
      if (look === "place") {
        const ring = new THREE.Mesh(new THREE.TorusGeometry(0.4 * size, 0.02, 6, 32), kit.additive(color, 0.6));
        ring.rotation.x = Math.PI / 2.4;
        grp.add(ring);
      }
      obj = grp;
    }
    obj.position.set(...world);
    if (g.ending) obj.visible = false;
    root.add(obj);
    stars.set(s.id, { obj, halo, world, base: obj.scale.x });
    picks.add(pickSphere(Math.max(0.6, size * 0.7), { type: "star", id: s.id }, world));
  }

  // Never raycast the visuals; only the pick spheres.
  root.traverse((o) => {
    o.raycast = () => {};
  });
  const coreIds = new Set(neurons.map((n) => n.id));
  if (u?.self_id) coreIds.add(u.self_id);
  return { root, picks, ticks, galaxies, stars, roots, hub, coreShimmer: core.shimmer, starsById, areasById, coreIds, starWorldPos };
}

function dispose(o: THREE.Object3D) {
  o.traverse((x) => {
    const m = x as THREE.Mesh;
    m.geometry?.dispose?.();
    const mat = m.material as THREE.Material | THREE.Material[] | undefined;
    if (Array.isArray(mat)) mat.forEach((y) => y.dispose());
    else mat?.dispose?.();
  });
}

function World({ universe, clusters, assistantState, lit, litSeq, memorySeq, perf, admin, privateMode, compact, selected, onSelect }: Props) {
  const built = useMemo(() => build(universe, perf, admin), [universe, perf, admin]);
  useEffect(() => () => dispose(built.root), [built]);
  const { invalidate } = useThree();
  const controls = useRef<CameraControls>(null);
  const [hover, setHover] = useState<{ pick: Pick; at: Vec3 } | null>(null);
  const still = perf.reducedMotion;

  // Live state the frame loop reads without re-rendering.
  const live = useRef({ lit, litAt: 0, memorySeq, memoryAt: -10, thinkingAt: -10, assistantState, clusters });
  const prevState = useRef(assistantState);
  useEffect(() => {
    live.current.lit = lit;
    live.current.litAt = performance.now() / 1000;
    invalidate();
  }, [lit, litSeq, invalidate]);
  useEffect(() => {
    if (memorySeq !== live.current.memorySeq) live.current.memoryAt = performance.now() / 1000;
    live.current.memorySeq = memorySeq;
  }, [memorySeq]);
  useEffect(() => {
    if (prevState.current === "idle" && assistantState !== "idle") live.current.thinkingAt = performance.now() / 1000;
    prevState.current = assistantState;
    live.current.assistantState = assistantState;
    live.current.clusters = clusters;
    invalidate();
  }, [assistantState, clusters, invalidate]);

  // The ripple a sent message makes, and the spark a saved memory makes.
  const fx = useMemo(() => {
    const ripple = kit.sprite(0x9fd8ff, 1, 0, kit.tex().soft);
    const spark = kit.sprite(0xbfe6ff, 1.4, 0);
    const path = new THREE.CatmullRomCurve3([
      new THREE.Vector3(HUB_POS[0] - 0.4, HUB_POS[1] - 0.2, HUB_POS[2]),
      new THREE.Vector3(7, 3, 3.6),
      new THREE.Vector3(3.2, 1.2, 1.4),
      new THREE.Vector3(0, 0, 0),
    ]);
    return { ripple, spark, path };
  }, []);

  useFrame((state, dt) => {
    const t = still ? 0 : state.clock.elapsedTime;
    for (const f of built.ticks) f(t, dt);
    // Galaxy dust is sized in pixels for this canvas.
    for (const g of built.galaxies.values()) {
      g.material.uniforms.uTime.value = t;
      g.material.uniforms.uScale.value = state.size.height / 2;
    }
    const now = performance.now() / 1000;
    const L = live.current;
    // Decks: lit while their layer works.
    const act = deckActivity({ assistant: L.assistantState, teams: L.clusters, recalling: L.lit.size > 0, routinesDue: false });
    for (const [deck, mats] of Object.entries(built.hub.lights)) {
      const level = act[deck as keyof typeof act] ?? 0.3;
      const pulse = level > 0.9 && !still ? 0.85 + 0.15 * Math.sin(t * 5) : 1;
      for (const m of mats!) m.emissiveIntensity = (m.userData.base as number) * (0.25 + 0.95 * level) * pulse;
    }
    // Recall: what was drawn on glows, its galaxy brightens, its root carries light, and the
    // core shimmers from inside if any of it was about who you are.
    const age = now - L.litAt;
    const fade = L.lit.size ? Math.max(0, 1 - Math.max(0, age - 12) / 3) : 0;
    const litAreas = new Set<string>();
    for (const [id, s] of built.stars) {
      const on = L.lit.has(id) ? fade : 0;
      const area = built.starsById.get(id)?.area;
      if (on && area) litAreas.add(area);
      const k = s.base * (1 + on * (0.6 + 0.2 * Math.sin(t * 6)));
      s.obj.scale.setScalar(k);
    }
    for (const id of L.lit) {
      const area = built.starsById.get(id)?.area;
      if (area) litAreas.add(area);
    }
    for (const [id, g] of built.galaxies) g.material.uniforms.uLit.value = litAreas.has(id) ? fade : 0;
    for (const [id, r] of built.roots) {
      const on = litAreas.has(id) ? fade : 0;
      (r.line.material as THREE.MeshBasicMaterial).opacity = (r.line.userData.base as number) + on * 0.7;
      (r.halo.material as THREE.MeshBasicMaterial).opacity = 0.015 + on * 0.12;
    }
    const coreLit = [...L.lit].some((id) => built.coreIds.has(id)) ? fade : 0;
    built.coreShimmer.uniforms.shimmer.value = coreLit * (0.4 + 0.3 * Math.sin(t * 3));
    // A sent message: a soft ripple leaves the core.
    const rp = now - L.thinkingAt;
    fx.ripple.material.opacity = rp < 2.2 ? 0.35 * (1 - rp / 2.2) : 0;
    fx.ripple.scale.setScalar(CORE_R * 2 + rp * 14);
    // A saved memory: a spark arcs from the archive deck into the core.
    const mp = now - L.memoryAt;
    if (mp < 1.6) {
      fx.spark.material.opacity = 0.95;
      fx.spark.position.copy(fx.path.getPoint(Math.min(1, mp / 1.6)));
    } else fx.spark.material.opacity = 0;
    // Drift slowly when nobody is looking around.
    if (!still && controls.current && !selected && !hover) controls.current.azimuthAngle += dt * 0.012;
  });

  // Fly to what was picked.
  useEffect(() => {
    const c = controls.current;
    if (!c) return;
    const go = (eye: Vec3, at: Vec3) => void c.setLookAt(...eye, ...at, !still);
    if (!selected) return go(compact ? [0, 32, 78] : DEFAULT_EYE, [0, 0, 0]);
    if (selected.type === "core") return go([0, 3, 15], [0, 0, 0]);
    if (selected.type === "hub") return go([HUB_POS[0] + 3, HUB_POS[1] + 2, HUB_POS[2] + 11], HUB_POS);
    if (selected.type === "area") {
      const g = built.galaxies.get(selected.area.id)?.layout;
      if (!g) return;
      // Face the disc, from the side the default view sees it from.
      const n = applyEuler([0, 1, 0], g.rot);
      const [x, y, z] = g.center;
      const d = g.radius * 2.3;
      return go([x + n[0] * d, y + n[1] * d, z + n[2] * d], g.center);
    }
    if (selected.type === "star") {
      const w = built.starWorldPos.get(selected.star.id);
      const g = selected.star.area ? built.galaxies.get(selected.star.area)?.layout : undefined;
      if (!w || !g) return;
      const n = applyEuler([0, 1, 0], g.rot);
      return go([w[0] + n[0] * 8, w[1] + n[1] * 8 + 1, w[2] + n[2] * 8], w);
    }
  }, [selected, built, compact, still]);

  // Galaxies other than the one in focus fade back.
  useEffect(() => {
    const focus =
      selected?.type === "area" ? selected.area.id : selected?.type === "star" ? selected.star.area : null;
    for (const [id, g] of built.galaxies) {
      const ending = g.layout.ending;
      g.material.uniforms.uDim.value = ending ? 0.3 : focus && id !== focus ? 0.35 : 1;
    }
    invalidate();
  }, [selected, built, invalidate]);

  const onPick = (e: ThreeEvent<MouseEvent>) => {
    const p = e.object.userData.pick as Pick | undefined;
    if (!p) return;
    e.stopPropagation();
    if (p.type === "core") onSelect({ type: "core" });
    else if (p.type === "hub") onSelect({ type: "hub" });
    else if (p.type === "area") {
      const area = built.areasById.get(p.id);
      if (area) onSelect({ type: "area", area });
    } else if (p.type === "star") {
      const star = built.starsById.get(p.id);
      if (star) onSelect({ type: "star", star });
    } else {
      const cluster = clusters.find((c) => c.id === p.id);
      if (cluster) onSelect({ type: "team", cluster });
    }
  };
  const onMove = (e: ThreeEvent<PointerEvent>) => {
    const p = e.object.userData.pick as Pick | undefined;
    if (!p) return;
    e.stopPropagation();
    const at = e.object.getWorldPosition(new THREE.Vector3());
    setHover((h) => (h && samePick(h.pick, p) ? h : { pick: p, at: [at.x, at.y, at.z] }));
    document.body.style.cursor = "pointer";
  };
  const onOut = () => {
    setHover(null);
    document.body.style.cursor = "";
  };
  useEffect(() => () => void (document.body.style.cursor = ""), []);

  return (
    <>
      <primitive object={built.root} />
      <primitive object={fx.ripple} />
      <primitive object={fx.spark} />
      <primitive object={built.picks} onClick={onPick} onPointerMove={onMove} onPointerOut={onOut} />
      <Fleet
        clusters={clusters}
        stars={universe?.stars ?? []}
        starWorldPos={built.starWorldPos}
        still={still}
        lite={perf.lite}
        onPick={(c) => onSelect({ type: "team", cluster: c })}
        onHover={(c, at) => setHover(c ? { pick: { type: "team", id: c.id }, at } : null)}
      />
      {hover && !privateMode && (
        <Html position={hover.at} center zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
          <HoverLabel pick={hover.pick} built={built} clusters={clusters} />
        </Html>
      )}
      <CameraControls
        ref={controls}
        makeDefault
        minDistance={4}
        maxDistance={260}
        smoothTime={0.6}
        draggingSmoothTime={0.15}
        truckSpeed={0.6}
      />
    </>
  );
}

function samePick(a: Pick, b: Pick) {
  return a.type === b.type && ("id" in a ? a.id : "") === ("id" in b ? b.id : "");
}

function HoverLabel({ pick, built, clusters }: { pick: Pick; built: Built; clusters: OrbCluster[] }) {
  let title = "";
  let line = "";
  if (pick.type === "core") {
    title = "Your core";
    line = "Who you are. Tap to look closer";
  } else if (pick.type === "hub") {
    title = "Nexus hub";
    line = "Tap to see each deck";
  } else if (pick.type === "area") {
    const a = built.areasById.get(pick.id);
    if (!a) return null;
    title = a.name;
    line = areaLine(a, [...built.starsById.values()]);
  } else if (pick.type === "star") {
    const s = built.starsById.get(pick.id);
    if (!s) return null;
    title = s.name;
  } else {
    const c = clusters.find((x) => x.id === pick.id);
    if (!c) return null;
    title = c.name;
    line = `${c.nodes.length} aboard`;
  }
  return (
    <div className="pointer-events-none -translate-y-8 whitespace-nowrap rounded-md border border-white/10 bg-[#070a14]/85 px-2 py-1 text-center text-xs text-slate-200 shadow-lg backdrop-blur">
      <div className="font-medium">{title}</div>
      {line && <div className="text-[11px] text-slate-400">{line}</div>}
    </div>
  );
}

// --- Fleet: a ship per team, crewed -------------------------------------------------------------

interface ShipState {
  id: string;
  group: THREE.Group;
  paint: (c: THREE.ColorRepresentation) => void;
  crew: { node: OrbNode; status: THREE.MeshStandardMaterial; fig: THREE.Group }[];
  born: number;
  type: string;
}

function Fleet({
  clusters,
  stars,
  starWorldPos,
  still,
  lite,
  onPick,
  onHover,
}: {
  clusters: OrbCluster[];
  stars: UniverseStar[];
  starWorldPos: Map<string, Vec3>;
  still: boolean;
  lite: boolean;
  onPick: (c: OrbCluster) => void;
  onHover: (c: OrbCluster | null, at: Vec3) => void;
}) {
  const group = useMemo(() => new THREE.Group(), []);
  const ships = useRef(new Map<string, ShipState>());
  const live = useMemo(() => clusters.filter((c) => c.lifetime !== "template"), [clusters]);

  // Build or update a ship per team.
  useEffect(() => {
    const seen = new Set<string>();
    for (const c of live) {
      seen.add(c.id);
      const type = shipType(c);
      let s = ships.current.get(c.id);
      const crewKey = c.nodes.map((n) => `${n.key}:${n.role}:${n.tier}`).join("|");
      if (s && (s.type !== type || s.group.userData.crewKey !== crewKey)) {
        group.remove(s.group);
        dispose(s.group);
        s = undefined;
      }
      if (!s) {
        const sh = kit.ship(type);
        const g = new THREE.Group();
        g.add(sh.group);
        g.userData.crewKey = crewKey;
        const crew = c.nodes.slice(0, lite ? 4 : 8).map((node, i, all) => {
          const m = kit.crewMember(crewRole(node.role));
          const fig = m.group;
          fig.scale.setScalar(0.32 * tierScale(node.tier));
          const a = (i / all.length) * Math.PI * 2;
          fig.position.set(Math.cos(a) * 2.2, 0.6 + Math.sin(i) * 0.3, Math.sin(a) * 2.2);
          fig.lookAt(0, fig.position.y, 0);
          g.add(fig);
          return { node, status: m.status, fig };
        });
        const pick = pickSphere(type === "carrier" ? 5 : 2.6, { type: "team", id: c.id }, [0, 0, 0]);
        g.add(pick);
        g.traverse((o) => {
          if (o !== pick) o.raycast = () => {};
        });
        s = { id: c.id, group: g, paint: sh.paint, crew, born: performance.now() / 1000, type };
        ships.current.set(c.id, s);
        group.add(g);
      }
      s.paint(stateColor(c.status));
      for (const m of s.crew) {
        const node = c.nodes.find((n) => n.key === m.node.key) ?? m.node;
        const l = crewLight(node.status);
        m.status.emissive.set(l.color);
        m.status.emissiveIntensity = l.on;
        m.node = node;
      }
    }
    for (const [id, s] of ships.current) {
      if (!seen.has(id)) {
        group.remove(s.group);
        dispose(s.group);
        ships.current.delete(id);
      }
    }
  }, [live, group, lite]);
  useEffect(
    () => () => {
      for (const s of ships.current.values()) dispose(s.group);
      ships.current.clear();
    },
    [],
  );

  const targets = useMemo(() => {
    const out = new Map<string, Vec3 | null>();
    for (const c of live) {
      const st = teamTarget(c, stars);
      out.set(c.id, st ? (starWorldPos.get(st.id) ?? null) : null);
    }
    return out;
  }, [live, stars, starWorldPos]);

  useFrame((state) => {
    const t = still ? 0 : state.clock.elapsedTime;
    const now = performance.now() / 1000;
    let i = 0;
    for (const c of live) {
      const s = ships.current.get(c.id);
      if (!s) continue;
      const target = targets.get(c.id);
      const hubOut = new THREE.Vector3(HUB_POS[0] + 2, HUB_POS[1] + 1, HUB_POS[2] + 1);
      // Waiting for you: hold still, blink amber. Done or failed: head home.
      const waiting = c.status === "awaiting_approval";
      const home = ["completed", "failed", "cancelled"].includes(c.status) || !["queued", "running", "awaiting_approval"].includes(c.status);
      const k = Math.min(1, (now - s.born) / 6);
      const ease = k * k * (3 - 2 * k);
      let pos: THREE.Vector3;
      let ahead: THREE.Vector3;
      if (home || !target) {
        // Hold station around the hub, each ship on its own slot.
        const a = (waiting || still ? 0 : t * 0.15) + i * 1.3;
        const r = home ? 6.5 : 9;
        pos = new THREE.Vector3(HUB_POS[0] + Math.cos(a) * r, HUB_POS[1] + 1.5 + (i % 3) * 1.2, HUB_POS[2] + Math.sin(a) * r);
        ahead = new THREE.Vector3(HUB_POS[0] + Math.cos(a + 0.1) * r, pos.y, HUB_POS[2] + Math.sin(a + 0.1) * r);
      } else {
        const tgt = new THREE.Vector3(...target);
        const a = (waiting || still ? 0 : t * 0.35) + i;
        const orbit = new THREE.Vector3(tgt.x + Math.cos(a) * 3, tgt.y + 1.5, tgt.z + Math.sin(a) * 3);
        const mid = hubOut.clone().lerp(orbit, 0.5).add(new THREE.Vector3(0, 6, 0));
        const curve = new THREE.QuadraticBezierCurve3(hubOut, mid, orbit);
        if (ease < 1) {
          pos = curve.getPoint(ease);
          ahead = curve.getPoint(Math.min(1, ease + 0.02));
        } else {
          pos = orbit;
          ahead = new THREE.Vector3(tgt.x + Math.cos(a + 0.1) * 3, orbit.y, tgt.z + Math.sin(a + 0.1) * 3);
        }
      }
      s.group.position.copy(pos);
      if (!waiting) s.group.lookAt(ahead);
      s.group.scale.setScalar(s.type === "carrier" ? 0.45 : 0.6);
      if (waiting) s.paint(Math.sin(t * 4) > 0 ? "#ffb347" : "#5a3a10");
      // Crew bob gently around their ship.
      s.crew.forEach((m, j) => (m.fig.position.y = 0.6 + Math.sin(t * 1.4 + j) * 0.15));
      i++;
    }
  });

  return (
    <primitive
      object={group}
      onClick={(e: ThreeEvent<MouseEvent>) => {
        const p = e.object.userData.pick as Pick | undefined;
        if (p?.type !== "team") return;
        e.stopPropagation();
        const c = clusters.find((x) => x.id === p.id);
        if (c) onPick(c);
      }}
      onPointerMove={(e: ThreeEvent<PointerEvent>) => {
        const p = e.object.userData.pick as Pick | undefined;
        if (p?.type !== "team") return;
        e.stopPropagation();
        const c = clusters.find((x) => x.id === p.id);
        const at = e.object.getWorldPosition(new THREE.Vector3());
        if (c) onHover(c, [at.x, at.y, at.z]);
      }}
      onPointerOut={() => onHover(null, [0, 0, 0])}
    />
  );
}
