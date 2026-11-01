/** The universe's building blocks, all made from simple shapes in code (no models to
 * download): the core, galaxies, the Nexus hub, the crew, ships and the small structures that
 * live in galaxies. Each maker returns a THREE object; anything that moves adds a tick to the
 * list it is given, called every frame with the time in seconds. */
import * as THREE from "three";
import type { CrewRole, Deck, ShipType } from "./model";

export type Tick = (t: number, dt: number) => void;

/** A seeded random source, so the same data always draws the same picture. */
export function seeded(seed: number) {
  let s = seed | 0;
  const next = () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return { next, range: (a: number, b: number) => a + next() * (b - a) };
}
export type Rng = ReturnType<typeof seeded>;

export function hashOf(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

const V = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);

// ---------- textures ----------
function radial(stops: [number, string][], size = 128): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d")!;
  const gr = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  stops.forEach(([o, col]) => gr.addColorStop(o, col));
  g.fillStyle = gr;
  g.fillRect(0, 0, size, size);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

let textures: { glow: THREE.Texture; soft: THREE.Texture; cloud: THREE.Texture } | null = null;
export function tex() {
  if (textures) return textures;
  const rng = seeded(11);
  const s = 256;
  const c = document.createElement("canvas");
  c.width = c.height = s;
  const g = c.getContext("2d")!;
  for (let i = 0; i < 60; i++) {
    const x = s / 2 + rng.range(-70, 70);
    const y = s / 2 + rng.range(-50, 50);
    const r = rng.range(20, 80);
    const gr = g.createRadialGradient(x, y, 0, x, y, r);
    gr.addColorStop(0, `rgba(255,255,255,${rng.range(0.04, 0.1)})`);
    gr.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = gr;
    g.fillRect(0, 0, s, s);
  }
  const cloud = new THREE.CanvasTexture(c);
  cloud.colorSpace = THREE.SRGBColorSpace;
  textures = {
    glow: radial([
      [0, "rgba(255,255,255,1)"],
      [0.15, "rgba(255,255,255,.85)"],
      [0.4, "rgba(255,255,255,.25)"],
      [1, "rgba(255,255,255,0)"],
    ]),
    soft: radial([
      [0, "rgba(255,255,255,.9)"],
      [0.5, "rgba(255,255,255,.25)"],
      [1, "rgba(255,255,255,0)"],
    ]),
    cloud,
  };
  return textures;
}

function gridTex(color = "#7fd4ff", lines = false, rng = seeded(3)) {
  const s = 256;
  const c = document.createElement("canvas");
  c.width = c.height = s;
  const g = c.getContext("2d")!;
  g.fillStyle = "rgba(20,60,110,0.25)";
  g.fillRect(0, 0, s, s);
  g.strokeStyle = color;
  g.globalAlpha = 0.9;
  g.lineWidth = 2;
  if (lines) {
    for (let y = 30; y < s - 20; y += 22) {
      g.beginPath();
      g.moveTo(24, y);
      g.lineTo(24 + rng.range(90, 200), y);
      g.stroke();
    }
  } else {
    g.strokeRect(6, 6, s - 12, s - 12);
    g.globalAlpha = 0.35;
    for (let i = 32; i < s; i += 32) {
      g.beginPath();
      g.moveTo(i, 6);
      g.lineTo(i, s - 6);
      g.moveTo(6, i);
      g.lineTo(s - 6, i);
      g.stroke();
    }
    g.globalAlpha = 1;
    g.lineWidth = 3;
    g.strokeRect(50, 60, 90, 70);
    g.strokeRect(150, 90, 60, 110);
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

// ---------- materials ----------
let materials: Record<"hull" | "dark" | "suit" | "metal", THREE.MeshStandardMaterial> | null = null;
export function mats() {
  if (materials) return materials;
  materials = {
    hull: new THREE.MeshStandardMaterial({ color: 0xd5dcea, metalness: 0.55, roughness: 0.32, flatShading: true }),
    dark: new THREE.MeshStandardMaterial({ color: 0x29324d, metalness: 0.6, roughness: 0.45, flatShading: true }),
    suit: new THREE.MeshStandardMaterial({ color: 0xeef1f7, metalness: 0.05, roughness: 0.55 }),
    metal: new THREE.MeshStandardMaterial({ color: 0x8e9bb8, metalness: 0.75, roughness: 0.3, flatShading: true }),
  };
  return materials;
}

export const glowMat = (color: THREE.ColorRepresentation, intensity = 3) =>
  new THREE.MeshStandardMaterial({ color: 0x000000, emissive: color, emissiveIntensity: intensity });

export const additive = (color: THREE.ColorRepresentation, opacity = 1) =>
  new THREE.MeshBasicMaterial({
    color,
    transparent: true,
    opacity,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });

export function sprite(color: THREE.ColorRepresentation, size: number, opacity = 1, map?: THREE.Texture) {
  const s = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: map ?? tex().glow,
      color,
      transparent: true,
      opacity,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    }),
  );
  s.scale.set(size, size, 1);
  return s;
}

function mesh(geo: THREE.BufferGeometry, mat: THREE.Material, x = 0, y = 0, z = 0) {
  const m = new THREE.Mesh(geo, mat);
  m.position.set(x, y, z);
  return m;
}

/** A mesh turned before it is added (rotating `add`'s return would turn its parent). */
function turned(m: THREE.Mesh, rx = 0, ry = 0, rz = 0) {
  m.rotation.set(rx, ry, rz);
  return m;
}

// ---------- space ----------
export function starfield(n: number, r = 600, rng = seeded(1)) {
  const g = new THREE.BufferGeometry();
  const p: number[] = [];
  const c: number[] = [];
  for (let i = 0; i < n; i++) {
    const u = rng.range(-1, 1);
    const th = rng.range(0, Math.PI * 2);
    const s = Math.sqrt(1 - u * u);
    const rr = r * rng.range(0.7, 1);
    p.push(Math.cos(th) * s * rr, u * rr, Math.sin(th) * s * rr);
    const col = new THREE.Color().setHSL(rng.range(0.55, 0.7), rng.range(0.2, 0.6), rng.range(0.6, 0.95));
    c.push(col.r, col.g, col.b);
  }
  g.setAttribute("position", new THREE.Float32BufferAttribute(p, 3));
  g.setAttribute("color", new THREE.Float32BufferAttribute(c, 3));
  return new THREE.Points(
    g,
    new THREE.PointsMaterial({
      size: 1.6,
      map: tex().glow,
      vertexColors: true,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      sizeAttenuation: false,
      opacity: 0.8,
    }),
  );
}

export function nebula(color: THREE.ColorRepresentation, pos: THREE.Vector3, size: number, opacity = 0.35, rot = 0) {
  const s = sprite(color, size, opacity, tex().cloud);
  s.position.copy(pos);
  s.material.rotation = rot;
  return s;
}

export function lights(group: THREE.Object3D) {
  group.add(new THREE.HemisphereLight(0x8fb2ff, 0x0b0d1a, 0.55));
  const k = new THREE.DirectionalLight(0xffe2c0, 2.4);
  k.position.set(8, 6, 10);
  const r = new THREE.DirectionalLight(0x6fa8ff, 3.2);
  r.position.set(-10, 4, -8);
  const b = new THREE.DirectionalLight(0xb48cff, 1.2);
  b.position.set(0, -8, -4);
  group.add(k, r, b);
}

/** A galaxy's disc: spiral arms of particles turning on the GPU (inner faster), coloured
 * from `hue` at the centre to `hue2` at the rim. Returns the group and its material, whose
 * `uDim` and `uLit` uniforms dim it while another galaxy is in focus or light it on recall. */
export function galaxy(opts: {
  radius: number;
  arms: number;
  n: number;
  hue: number;
  hue2: number;
  seed: number;
  twist?: number;
  thick?: number;
  newborn?: boolean;
}) {
  const { radius, arms, n, hue, hue2, seed, twist = 2.3, thick = 0.5, newborn = false } = opts;
  const rng = seeded(seed);
  const grp = new THREE.Group();
  const g = new THREE.BufferGeometry();
  const p: number[] = [];
  const c: number[] = [];
  const size: number[] = [];
  const core = new THREE.Color(0xfff0d8);
  for (let i = 0; i < n; i++) {
    const t = Math.pow(rng.next(), 1.4);
    const r = t * radius;
    const arm = Math.floor(rng.range(0, arms));
    const a =
      (arm / arms) * Math.PI * 2 +
      t * twist * Math.PI +
      rng.range(-0.22, 0.22) * (1 - t * 0.5) +
      (newborn ? rng.range(-2, 2) : 0);
    const sp = rng.range(-1, 1) * radius * 0.035 * (1 + t);
    p.push(
      Math.cos(a) * r + sp,
      rng.range(-1, 1) * thick * (1 - t) * (newborn ? 3 : 1),
      Math.sin(a) * r + rng.range(-1, 1) * radius * 0.035,
    );
    // Mostly fine dust, with the odd bright star.
    size.push(rng.next() < 0.04 ? rng.range(1.6, 2.6) : rng.range(0.5, 1.2));
    const col = new THREE.Color().setHSL(
      THREE.MathUtils.lerp(hue, hue2, t) % 1,
      0.8,
      THREE.MathUtils.lerp(0.66, 0.5, t),
    );
    if (t < 0.08) col.lerp(core, (1 - t / 0.08) * 0.7);
    c.push(col.r, col.g, col.b);
  }
  g.setAttribute("position", new THREE.Float32BufferAttribute(p, 3));
  g.setAttribute("color", new THREE.Float32BufferAttribute(c, 3));
  g.setAttribute("aSize", new THREE.Float32BufferAttribute(size, 1));
  const material = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    vertexColors: true,
    uniforms: {
      uTime: { value: 0 },
      uSize: { value: radius * 0.022 },
      uScale: { value: 400 },
      uDim: { value: 1 },
      uLit: { value: 0 },
      uMap: { value: tex().glow },
      uSpeed: { value: 0.35 / Math.max(3, radius) },
    },
    vertexShader: `
      uniform float uTime; uniform float uSize; uniform float uScale; uniform float uSpeed;
      attribute float aSize;
      varying vec3 vColor;
      void main() {
        vColor = color;
        float r = length(position.xz);
        float a = uTime * uSpeed * (1.6 / (0.25 + r * 0.12));
        float cs = cos(a), sn = sin(a);
        vec3 p = vec3(position.x * cs - position.z * sn, position.y, position.x * sn + position.z * cs);
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        gl_PointSize = max(1.0, uSize * aSize * uScale / -mv.z);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `
      uniform sampler2D uMap; uniform float uDim; uniform float uLit;
      varying vec3 vColor;
      void main() {
        vec4 t = texture2D(uMap, gl_PointCoord);
        gl_FragColor = vec4(vColor * (uDim + uLit * 0.8), t.a * 0.7 * uDim);
      }`,
  });
  const pts = new THREE.Points(g, material);
  grp.add(pts);
  const glow = sprite(core, radius * (newborn ? 0.25 : 0.32) * 0.5, newborn ? 0.5 : 0.7);
  grp.add(glow);
  const haze = sprite(new THREE.Color().setHSL(hue, 0.7, 0.45), radius * 2.2, 0.08, tex().cloud);
  grp.add(haze);
  return { group: grp, material, glow, haze };
}

/** A soft glowing tube along a curve, with a thin bright line and a wide faint halo. */
export function tube(points: THREE.Vector3[], color: THREE.ColorRepresentation, radius = 0.06, opacity = 0.8) {
  const curve = new THREE.CatmullRomCurve3(points);
  const grp = new THREE.Group();
  const line = new THREE.Mesh(new THREE.TubeGeometry(curve, 60, radius, 6), additive(color, opacity));
  const halo = new THREE.Mesh(new THREE.TubeGeometry(curve, 60, radius * 3, 6), additive(color, opacity * 0.07));
  grp.add(line, halo);
  return { group: grp, curve, line, halo };
}

/** Lights running along a curve. */
export function flowDots(curve: THREE.Curve<THREE.Vector3>, color: THREE.ColorRepresentation, n: number, speed: number, size: number, ticks: Tick[]) {
  const grp = new THREE.Group();
  const dots = Array.from({ length: n }, () => {
    const s = sprite(color, size, 0.9);
    grp.add(s);
    return s;
  });
  ticks.push((t) => dots.forEach((d, i) => d.position.copy(curve.getPoint((t * speed + i / n) % 1))));
  return grp;
}

/** A glass sphere: a fresnel rim, nearly clear in the middle. */
export function glass(radius: number, color: THREE.ColorRepresentation, power = 2.8, strength = 0.85) {
  const mat = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      c: { value: new THREE.Color(color) },
      p: { value: power },
      s: { value: strength },
      shimmer: { value: 0 },
    },
    vertexShader:
      "varying vec3 vN; varying vec3 vV; void main(){ vec4 mv = modelViewMatrix*vec4(position,1.); vN = normalize(normalMatrix*normal); vV = normalize(-mv.xyz); gl_Position = projectionMatrix*mv; }",
    fragmentShader:
      "uniform vec3 c; uniform float p; uniform float s; uniform float shimmer; varying vec3 vN; varying vec3 vV; void main(){ float f = pow(1.-max(dot(vN,vV),0.), p); gl_FragColor = vec4(c*(f*s*(1.+shimmer)+.015+shimmer*.05), f*.9+.02); }",
  });
  return new THREE.Mesh(new THREE.SphereGeometry(radius, 72, 48), mat);
}

/** The core: a glass sphere holding a network of who you are. Never labelled. `neurons` are
 * unit-sphere positions with size and glow; `links` index pairs with a weight. */
export function core(
  radius: number,
  neurons: { pos: [number, number, number]; size: number; glow: number; layer: number }[],
  links: [number, number, number][],
  hue: number,
  ticks: Tick[],
) {
  const grp = new THREE.Group();
  const shell = glass(radius, new THREE.Color().setHSL(hue, 0.8, 0.6));
  grp.add(shell);
  grp.add(sprite(new THREE.Color().setHSL(hue + 0.02, 0.7, 0.5), radius * 2.6, 0.08, tex().soft));
  const inner = new THREE.Group();
  grp.add(inner);
  const rng = seeded(5);
  // With nothing known yet, a faint seed network so the core is never empty.
  const pts = neurons.length
    ? neurons
    : Array.from({ length: 24 }, (_, i) => {
        const u = rng.range(-1, 1);
        const th = rng.range(0, Math.PI * 2);
        const r = rng.range(0.1, 0.8);
        const s = Math.sqrt(1 - u * u);
        return { pos: [Math.cos(th) * s * r, u * r, Math.sin(th) * s * r] as [number, number, number], size: 0.2, glow: 0.3, layer: i % 3 };
      });
  const pos = pts.map((n) => V(n.pos[0] * radius, n.pos[1] * radius, n.pos[2] * radius));
  const cols = pts.map((n) =>
    new THREE.Color().setHSL(([0.58, 0.08, 0.78][n.layer] + hue - 0.58 + 1) % 1, 0.95, 0.38 + 0.2 * n.glow),
  );
  const inst = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1, 1), new THREE.MeshBasicMaterial({ color: 0xffffff }), pts.length);
  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  pts.forEach((n, i) => {
    const sc = radius * (n.layer === 0 ? 0.022 : 0.014) * (0.7 + n.size * 0.9);
    m.compose(pos[i], q, V(sc, sc, sc));
    inst.setMatrixAt(i, m);
    inst.setColorAt(i, cols[i]);
  });
  inner.add(inst);
  // Links: the ones the data has, else each neuron to its nearest two.
  const pairs: [number, number, number][] = links.length
    ? links
    : pos.flatMap((p, i) =>
        pos
          .map((o, j) => [o.distanceTo(p), j] as const)
          .filter(([, j]) => j > i)
          .sort((a, b) => a[0] - b[0])
          .slice(0, 2)
          .filter(([d]) => d < radius * 0.55)
          .map(([, j]) => [i, j, 0.5] as [number, number, number]),
      );
  const lp: number[] = [];
  const lc: number[] = [];
  for (const [a, b] of pairs) {
    if (!pos[a] || !pos[b]) continue;
    lp.push(pos[a].x, pos[a].y, pos[a].z, pos[b].x, pos[b].y, pos[b].z);
    lc.push(cols[a].r, cols[a].g, cols[a].b, cols[b].r, cols[b].g, cols[b].b);
  }
  const lg = new THREE.BufferGeometry();
  lg.setAttribute("position", new THREE.Float32BufferAttribute(lp, 3));
  lg.setAttribute("color", new THREE.Float32BufferAttribute(lc, 3));
  inner.add(
    new THREE.LineSegments(
      lg,
      new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.28, blending: THREE.AdditiveBlending, depthWrite: false }),
    ),
  );
  // Synapse pulses along a few links.
  const pulses = pairs.slice(0, 16).flatMap(([a, b]) => {
    if (!pos[a] || !pos[b]) return [];
    const s = sprite(0xffffff, radius * 0.05, 0.85);
    inner.add(s);
    return [{ s, a: pos[a], b: pos[b], o: rng.next() }];
  });
  const shimmer = shell.material as THREE.ShaderMaterial;
  ticks.push((t) => {
    pulses.forEach((p) => p.s.position.lerpVectors(p.a, p.b, (t * 0.35 + p.o) % 1));
    inner.rotation.y = t * 0.04;
    // A slow breath, plus whatever shimmer recall set.
    shimmer.uniforms.s.value = 0.85 + 0.08 * Math.sin(t * 0.6);
  });
  return { group: grp, shimmer };
}

// ---------- the Nexus hub ----------
function windowsRing(r: number, y: number, count: number, color: THREE.ColorRepresentation, w = 0.12, h = 0.08) {
  const mat = glowMat(color, 2.6);
  const inst = new THREE.InstancedMesh(new THREE.BoxGeometry(w, h, 0.02), mat, count);
  const o = new THREE.Object3D();
  for (let i = 0; i < count; i++) {
    const a = (i / count) * Math.PI * 2;
    o.position.set(Math.cos(a) * r, y, Math.sin(a) * r);
    o.lookAt(0, y, 0);
    o.updateMatrix();
    inst.setMatrixAt(i, o.matrix);
  }
  return { inst, mat };
}

/** Nexus as a station built in decks, one per layer of the system; Orion is the whole station.
 * Each deck's lights are returned so the scene can light the ones that are working. */
export function hub(admin: boolean, ticks: Tick[]) {
  const M = mats();
  const g = new THREE.Group();
  const lightsOf: Partial<Record<Deck, THREE.MeshStandardMaterial[]>> = {};
  const lit = (deck: Deck, mat: THREE.MeshStandardMaterial) => {
    (lightsOf[deck] ??= []).push(mat);
    mat.userData.base = mat.emissiveIntensity;
    return mat;
  };
  g.add(mesh(new THREE.CylinderGeometry(0.22, 0.22, 9.6, 12), M.metal));
  // Comms array
  const comms = new THREE.Group();
  comms.position.y = 4.4;
  comms.add(mesh(new THREE.CylinderGeometry(0.75, 0.95, 0.3, 12), M.hull));
  comms.add(turned(mesh(new THREE.SphereGeometry(0.7, 24, 12, 0, Math.PI * 2, 0, Math.PI / 3.2), M.hull, 0, 1.05, 0), Math.PI));
  comms.add(mesh(new THREE.CylinderGeometry(0.03, 0.03, 1.2, 6), M.metal, 0, 0.7, 0));
  const tipMat = lit("comms", glowMat(0xff5a7a, 4));
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * Math.PI * 2;
    comms.add(turned(mesh(new THREE.CylinderGeometry(0.025, 0.025, 1.4, 6), M.metal, Math.cos(a) * 0.6, 0.7, Math.sin(a) * 0.6), -Math.sin(a) * 0.35, 0, Math.cos(a) * 0.35));
    comms.add(mesh(new THREE.SphereGeometry(0.06, 8, 8), tipMat, Math.cos(a) * 0.85, 1.38, Math.sin(a) * 0.85));
  }
  g.add(comms);
  // Command bridge
  const bridge = new THREE.Group();
  bridge.position.y = 3.35;
  bridge.add(mesh(new THREE.CylinderGeometry(1.35, 1.1, 0.55, 20), M.hull));
  const dome = new THREE.MeshPhysicalMaterial({ color: 0x8fd0ff, metalness: 0.2, roughness: 0.05, transparent: true, opacity: 0.35, emissive: 0x2a6fb0, emissiveIntensity: 0.5 });
  lit("bridge", dome);
  bridge.add(mesh(new THREE.SphereGeometry(1.05, 28, 14, 0, Math.PI * 2, 0, Math.PI / 2), dome, 0, 0.27, 0));
  const bw = windowsRing(1.36, 0, 28, 0x9fdcff);
  lit("bridge", bw.mat);
  bridge.add(bw.inst);
  bridge.add(mesh(new THREE.CylinderGeometry(0.45, 0.45, 0.05, 24), lit("bridge", glowMat(0x5fd0ff, 2.4)), 0, 0.45, 0));
  const holo = sprite(0x6fd8ff, 1.3, 0.5);
  holo.position.y = 0.8;
  bridge.add(holo);
  g.add(bridge);
  // Hangar ring
  const hangar = new THREE.Group();
  hangar.position.y = 2.05;
  hangar.add(turned(mesh(new THREE.TorusGeometry(2.7, 0.42, 10, 36), M.hull), Math.PI / 2));
  for (let i = 0; i < 4; i++) hangar.add(turned(mesh(new THREE.BoxGeometry(5.4, 0.12, 0.18), M.dark), 0, (i / 4) * Math.PI));
  const bayMat = lit("hangar", glowMat(0xffc46b, 2.2));
  const bays = new THREE.InstancedMesh(new THREE.BoxGeometry(0.38, 0.26, 0.05), bayMat, 12);
  const o = new THREE.Object3D();
  for (let i = 0; i < 12; i++) {
    const a = (i / 12) * Math.PI * 2;
    o.position.set(Math.cos(a) * 3.13, 0, Math.sin(a) * 3.13);
    o.lookAt(0, 0, 0);
    o.updateMatrix();
    bays.setMatrixAt(i, o.matrix);
  }
  hangar.add(bays);
  g.add(hangar);
  // Workshop: skill crystals and sparks
  const work = new THREE.Group();
  work.position.y = 0.95;
  work.add(mesh(new THREE.CylinderGeometry(1.75, 1.75, 0.5, 8), M.hull));
  const ww = windowsRing(1.62, 0, 16, 0x9be7c4, 0.22, 0.1);
  lit("workshop", ww.mat);
  work.add(ww.inst);
  const crystal = lit("workshop", glowMat(0xb98cff, 2.6));
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2 + 0.3;
    const cr = mesh(new THREE.OctahedronGeometry(0.16, 0), crystal, Math.cos(a) * 1.25, 0.45, Math.sin(a) * 1.25);
    cr.scale.y = 1.8;
    work.add(cr);
  }
  g.add(work);
  // Archive vault
  const arch = new THREE.Group();
  arch.position.y = -0.15;
  arch.add(mesh(new THREE.CylinderGeometry(1.45, 1.45, 0.95, 24), M.dark));
  const strip = lit("archive", glowMat(0x6cc6ff, 1.8));
  for (let i = 0; i < 18; i++) {
    const a = (i / 18) * Math.PI * 2;
    arch.add(mesh(new THREE.BoxGeometry(0.06, 0.7, 0.06), strip, Math.cos(a) * 1.47, 0, Math.sin(a) * 1.47));
  }
  arch.add(turned(mesh(new THREE.TorusGeometry(1.5, 0.07, 8, 40), M.hull, 0, 0.5, 0), Math.PI / 2));
  arch.add(turned(mesh(new THREE.TorusGeometry(1.5, 0.07, 8, 40), M.hull, 0, -0.5, 0), Math.PI / 2));
  g.add(arch);
  // Clock ring
  const clock = new THREE.Group();
  clock.position.y = -1.25;
  clock.add(turned(mesh(new THREE.TorusGeometry(3.2, 0.06, 8, 120), M.metal), Math.PI / 2));
  const tick = lit("clock", glowMat(0x7cffd1, 3));
  for (let i = 0; i < 24; i++) {
    const a = (i / 24) * Math.PI * 2;
    clock.add(mesh(new THREE.BoxGeometry(0.08, 0.22, 0.08), i % 6 === 0 ? tick : M.hull, Math.cos(a) * 3.2, 0, Math.sin(a) * 3.2));
  }
  for (let i = 0; i < 3; i++) clock.add(turned(mesh(new THREE.BoxGeometry(6.2, 0.05, 0.05), M.metal), 0, (i / 3) * Math.PI));
  g.add(clock);
  // Reactor: one cell per model tier
  const reac = new THREE.Group();
  reac.position.y = -2.3;
  reac.add(mesh(new THREE.CylinderGeometry(1.15, 1.15, 0.7, 16), M.dark));
  const cells = [0x5fa8ff, 0x3fe0c5, 0xa58bff, 0xffc75a].map((c, i) => {
    const a = (i / 4) * Math.PI * 2 + 0.4;
    const mat = lit("reactor", glowMat(c, 2.6));
    reac.add(mesh(new THREE.SphereGeometry(0.28, 16, 12), mat, Math.cos(a) * 1.2, 0, Math.sin(a) * 1.2));
    const h = sprite(c, 1.1, 0.35);
    h.position.set(Math.cos(a) * 1.2, 0, Math.sin(a) * 1.2);
    reac.add(h);
    return mat;
  });
  reac.add(turned(mesh(new THREE.TorusGeometry(1.25, 0.05, 6, 40), M.metal), Math.PI / 2));
  g.add(reac);
  // Foundation
  const fnd = new THREE.Group();
  fnd.position.y = -3.35;
  fnd.add(mesh(new THREE.CylinderGeometry(1.9, 1.3, 0.75, 18), M.hull));
  const run = lit("foundation", glowMat(0x9effa8, 3));
  for (let i = 0; i < 24; i++) {
    const a = (i / 24) * Math.PI * 2;
    fnd.add(mesh(new THREE.SphereGeometry(0.045, 6, 6), run, Math.cos(a) * 1.82, 0.3, Math.sin(a) * 1.82));
  }
  fnd.add(turned(mesh(new THREE.ConeGeometry(0.5, 1, 12), M.metal, 0, -0.85, 0), Math.PI));
  g.add(fnd);
  // Scaffold: a module under construction (self-improvement)
  const sc = new THREE.Group();
  sc.position.set(2.75, 0.95, 0.3);
  const frame = new THREE.LineBasicMaterial({ color: 0xffd28a, transparent: true, opacity: 0.9 });
  sc.add(new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1.2, 0.9, 0.9, 2, 2, 2)), frame));
  sc.add(mesh(new THREE.BoxGeometry(1.2, 0.45, 0.9), M.hull, 0, -0.22, 0));
  sc.add(mesh(new THREE.BoxGeometry(0.9, 0.06, 0.06), M.metal, -0.95, 0, 0));
  g.add(sc);
  if (admin) {
    const ad = new THREE.Group();
    ad.position.set(-2.6, -2.3, 0);
    ad.add(mesh(new THREE.CylinderGeometry(0.7, 0.7, 0.55, 6), M.hull));
    const aw = windowsRing(0.66, 0, 10, 0xff9ad5, 0.18, 0.1);
    lit("admin", aw.mat);
    ad.add(aw.inst);
    ad.add(mesh(new THREE.BoxGeometry(1.2, 0.08, 0.08), M.metal, 0.9, 0, 0));
    g.add(ad);
  }
  ticks.push((t) => {
    hangar.rotation.y = t * 0.12;
    clock.rotation.y = -t * 0.05;
    comms.rotation.y = t * 0.2;
    holo.material.opacity = 0.35 + 0.15 * Math.sin(t * 2);
  });
  const decks: Record<Deck, number> = {
    comms: 4.9,
    bridge: 3.35,
    hangar: 2.05,
    workshop: 0.95,
    archive: -0.15,
    clock: -1.25,
    reactor: -2.3,
    foundation: -3.35,
    scaffold: 0.95,
    admin: -2.3,
  };
  return { group: g, lights: lightsOf, cells, decks, holo };
}

// ---------- crew ----------
function astronaut(visor: number, accent: number) {
  const M = mats();
  const g = new THREE.Group();
  g.add(mesh(new THREE.CapsuleGeometry(0.36, 0.5, 6, 12), M.suit, 0, 0.95, 0));
  g.add(mesh(new THREE.SphereGeometry(0.36, 20, 16), M.suit, 0, 1.68, 0));
  g.add(
    mesh(
      new THREE.SphereGeometry(0.3, 20, 12, -Math.PI * 0.45, Math.PI * 0.9, Math.PI * 0.28, Math.PI * 0.42),
      new THREE.MeshStandardMaterial({ color: 0x0a1226, metalness: 0.9, roughness: 0.1, emissive: visor, emissiveIntensity: 0.55 }),
      0,
      1.68,
      0.09,
    ),
  );
  g.add(turned(mesh(new THREE.TorusGeometry(0.3, 0.05, 6, 20), M.dark, 0, 1.38, 0), Math.PI / 2));
  for (const s of [-1, 1]) {
    g.add(turned(mesh(new THREE.CapsuleGeometry(0.11, 0.38, 4, 8), M.suit, s * 0.46, 1.0, 0), 0, 0, s * 0.25));
    g.add(mesh(new THREE.CapsuleGeometry(0.13, 0.32, 4, 8), M.suit, s * 0.17, 0.3, 0));
    g.add(mesh(new THREE.BoxGeometry(0.2, 0.12, 0.3), M.dark, s * 0.17, 0.04, 0.04));
  }
  g.add(mesh(new THREE.BoxGeometry(0.18, 0.12, 0.04), glowMat(accent, 2.5), 0, 1.1, 0.36));
  return g;
}

function botBody(color: number, eye: number) {
  const M = mats();
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color, metalness: 0.7, roughness: 0.3, flatShading: true });
  g.add(mesh(new THREE.BoxGeometry(0.7, 0.75, 0.5), mat, 0, 1.0, 0));
  g.add(mesh(new THREE.BoxGeometry(0.5, 0.38, 0.42), mat, 0, 1.62, 0));
  g.add(mesh(new THREE.BoxGeometry(0.38, 0.07, 0.02), glowMat(eye, 4), 0, 1.64, 0.22));
  g.add(mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.3, 6), M.metal, 0.15, 1.92, 0));
  g.add(mesh(new THREE.SphereGeometry(0.05, 8, 8), glowMat(eye, 4), 0.15, 2.08, 0));
  for (const s of [-1, 1]) {
    g.add(mesh(new THREE.BoxGeometry(0.14, 0.5, 0.14), mat, s * 0.48, 0.95, 0));
    g.add(mesh(new THREE.BoxGeometry(0.18, 0.55, 0.2), mat, s * 0.18, 0.35, 0));
  }
  g.add(turned(mesh(new THREE.CylinderGeometry(0.12, 0.12, 0.05, 16), glowMat(eye, 2), 0, 1.05, 0.26), Math.PI / 2));
  return g;
}

function drone(color: number, eye: number, size: number) {
  const M = mats();
  const g = new THREE.Group();
  g.add(mesh(new THREE.SphereGeometry(size, 20, 14), new THREE.MeshStandardMaterial({ color, metalness: 0.3, roughness: 0.35 }), 0, 1.2, 0));
  g.add(mesh(new THREE.SphereGeometry(size * 0.45, 14, 10), glowMat(eye, 2.8), 0, 1.2, size * 0.72));
  g.add(turned(mesh(new THREE.TorusGeometry(size * 1.35, 0.05, 6, 30), M.metal, 0, 1.2, 0), Math.PI / 2));
  return g;
}

/** One stylised character per role. Returns the figure and its status light, which the scene
 * turns amber while it waits for you and red when it failed. */
export function crewMember(role: CrewRole) {
  const M = mats();
  let g: THREE.Group;
  if (role === "research") {
    g = astronaut(0x5fe3ff, 0x5fa8ff);
    g.add(mesh(new THREE.BoxGeometry(0.6, 0.7, 0.3), M.hull, 0, 1.05, -0.42));
    g.add(turned(mesh(new THREE.SphereGeometry(0.28, 12, 6, 0, Math.PI * 2, 0, Math.PI / 3), M.hull, 0, 1.7, -0.48), -0.6));
    g.add(turned(mesh(new THREE.ConeGeometry(0.35, 1.4, 16, 1, true), additive(0x5fe3ff, 0.2), 0.62, 1.0, 0.9), Math.PI / 2 + 0.2));
  } else if (role === "architect") {
    g = botBody(0x9aa9c9, 0x6fd8ff);
    g.add(
      turned(
        mesh(
          new THREE.PlaneGeometry(1.1, 0.8),
          new THREE.MeshBasicMaterial({ map: gridTex(), transparent: true, opacity: 0.85, blending: THREE.AdditiveBlending, side: THREE.DoubleSide, depthWrite: false }),
          0,
          1.25,
          0.75,
        ),
        -0.55,
      ),
    );
  } else if (role === "designer") {
    g = drone(0xf1eaf7, 0xff7ad9, 0.42);
    g.add(turned(mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.8, 6), M.metal, 0.45, 1.0, 0.3), 0, 0, 0.9));
    [0xff7ad9, 0xffc46b, 0x6fd8ff].forEach((c, i) => g.add(mesh(new THREE.SphereGeometry(0.06, 8, 8), glowMat(c, 4), 0.95 + i * 0.18, 0.62 - i * 0.1, 0.3)));
  } else if (role === "builder") {
    g = botBody(0xd99a4e, 0xffc46b);
    g.scale.set(1.18, 1.05, 1.18);
    g.add(turned(mesh(new THREE.CylinderGeometry(0.06, 0.1, 0.6, 8), M.metal, 0.62, 0.8, 0.3), 1.0));
    g.add(mesh(new THREE.SphereGeometry(0.08, 8, 8), glowMat(0xffa040, 6), 0.62, 0.6, 0.6));
  } else if (role === "reviewer") {
    g = drone(0xdfe6f5, 0x7cffd1, 0.38);
    g.add(mesh(new THREE.TorusGeometry(0.42, 0.06, 8, 24), M.hull, 0, 1.2, 0.5));
    g.add(mesh(new THREE.CircleGeometry(0.4, 24), new THREE.MeshBasicMaterial({ color: 0x2fb3a0, transparent: true, opacity: 0.3 }), 0, 1.2, 0.52));
  } else if (role === "tester") {
    g = botBody(0x7f8fb0, 0x7cff9a);
    g.add(
      turned(
        mesh(new THREE.CylinderGeometry(0.48, 0.48, 0.06, 6), new THREE.MeshStandardMaterial({ color: 0x1a3a2c, emissive: 0x2eff8a, emissiveIntensity: 0.35, transparent: true, opacity: 0.85 }), -0.62, 1.0, 0.25),
        0,
        0.3,
        Math.PI / 2,
      ),
    );
  } else if (role === "deployer") {
    g = astronaut(0xffc46b, 0xffc46b);
    g.add(mesh(new THREE.BoxGeometry(0.55, 0.6, 0.25), M.dark, 0, 1.05, -0.38));
    for (const s of [-1, 1]) g.add(turned(mesh(new THREE.ConeGeometry(0.1, 0.55, 10, 1, true), additive(0x6fc8ff, 0.8), s * 0.16, 0.3, -0.42), Math.PI));
  } else if (role === "scribe") {
    g = botBody(0xc9d3e8, 0xa58bff);
    g.scale.set(0.88, 1.12, 0.88);
    g.add(
      turned(
        mesh(
          new THREE.PlaneGeometry(0.8, 1.0),
          new THREE.MeshBasicMaterial({ map: gridTex("#c9b6ff", true), transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, side: THREE.DoubleSide, depthWrite: false }),
          0.1,
          1.1,
          0.7,
        ),
        -0.3,
      ),
    );
  } else {
    g = new THREE.Group();
    g.add(mesh(new THREE.BoxGeometry(0.38, 0.2, 0.38), M.hull, 0, 1.0, 0));
    for (const [x, z] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
      g.add(turned(mesh(new THREE.TorusGeometry(0.16, 0.025, 6, 16), glowMat(0x6fd8ff, 1.6), x * 0.34, 1.06, z * 0.34), Math.PI / 2));
    }
    g.add(mesh(new THREE.BoxGeometry(0.16, 0.14, 0.16), glowMat(0xffc46b, 1.5), 0, 0.84, 0));
  }
  const status = glowMat(0x5fb4ff, 0);
  g.add(mesh(new THREE.SphereGeometry(0.09, 8, 8), status, 0, 2.3, 0));
  return { group: g, status };
}

// ---------- ships ----------
function trail(color: THREE.ColorRepresentation, len: number, width: number) {
  const curve = new THREE.LineCurve3(V(0, 0, 0), V(0, 0, -len));
  const geo = new THREE.TubeGeometry(curve, 16, width, 10, false);
  const pos = geo.attributes.position;
  const cols: number[] = [];
  for (let i = 0; i < pos.count; i++) {
    const z = -pos.getZ(i) / len;
    const k = Math.pow(1 - z, 2.2);
    cols.push(k, k, k);
    const s = 1 - z * 0.7;
    pos.setX(i, pos.getX(i) * s);
    pos.setY(i, pos.getY(i) * s);
  }
  geo.setAttribute("color", new THREE.Float32BufferAttribute(cols, 3));
  return new THREE.Mesh(
    geo,
    new THREE.MeshBasicMaterial({ color, vertexColors: true, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false }),
  );
}

/** A team's ship. Its engines and trail take the state colour, which `paint` changes. */
export function ship(type: ShipType) {
  const M = mats();
  const g = new THREE.Group();
  const engineMats: THREE.MeshStandardMaterial[] = [];
  const trails: THREE.MeshBasicMaterial[] = [];
  const glows: THREE.SpriteMaterial[] = [];
  const engine = (x: number, y: number, z: number, r: number, len: number) => {
    g.add(turned(mesh(new THREE.CylinderGeometry(r * 1.2, r * 1.5, 0.3, 12), M.dark, x, y, z), Math.PI / 2));
    const em = glowMat(0x5fb4ff, 5);
    engineMats.push(em);
    g.add(turned(mesh(new THREE.CircleGeometry(r * 1.1, 16), em, x, y, z - 0.16), 0, Math.PI));
    const s = sprite(0x5fb4ff, r * 9, 0.8);
    s.position.set(x, y, z - 0.3);
    glows.push(s.material);
    g.add(s);
    const t = trail(0x5fb4ff, len, r * 1.1);
    t.position.set(x, y, z - 0.2);
    trails.push(t.material as THREE.MeshBasicMaterial);
    g.add(t);
  };
  if (type === "scout") {
    g.add(turned(mesh(new THREE.CylinderGeometry(0.18, 0.38, 3, 10), M.hull), Math.PI / 2));
    g.add(turned(mesh(new THREE.ConeGeometry(0.18, 0.9, 10), M.hull, 0, 0, 1.95), Math.PI / 2));
    for (const s of [-1, 1]) g.add(turned(mesh(new THREE.BoxGeometry(1.4, 0.05, 0.7), M.dark, s * 0.7, 0, -0.6), 0, s * 0.35));
    g.add(mesh(new THREE.SphereGeometry(0.35, 12, 6, 0, Math.PI * 2, 0, Math.PI / 3), M.hull, 0, 0.45, 0.5));
    engine(0, 0, -1.55, 0.2, 5);
  } else if (type === "freighter") {
    g.add(mesh(new THREE.BoxGeometry(1.6, 0.9, 3.2), M.hull));
    g.add(mesh(new THREE.BoxGeometry(1.1, 0.6, 0.8), M.hull, 0, 0.2, 1.9));
    g.add(mesh(new THREE.BoxGeometry(0.9, 0.2, 0.05), glowMat(0x9fdcff, 2.5), 0, 0.3, 2.31));
    [0xff8a5c, 0x5fd4b0, 0xffd06b, 0x8fa8ff].forEach((c, i) =>
      g.add(mesh(new THREE.BoxGeometry(0.6, 0.5, 0.6), new THREE.MeshStandardMaterial({ color: c, roughness: 0.6, metalness: 0.2, flatShading: true }), i % 2 ? 0.4 : -0.4, 0.7, i < 2 ? 0.5 : -0.4)),
    );
    engine(-0.5, -0.1, -1.65, 0.26, 5);
    engine(0.5, -0.1, -1.65, 0.26, 5);
  } else if (type === "cutter") {
    const body = mesh(new THREE.OctahedronGeometry(0.7, 0), M.hull);
    body.scale.set(1, 0.6, 1.6);
    g.add(body);
    g.add(mesh(new THREE.TorusGeometry(1.0, 0.07, 8, 32), M.metal));
    for (let i = 0; i < 12; i++) {
      const a = (i / 12) * Math.PI * 2;
      g.add(mesh(new THREE.SphereGeometry(0.06, 6, 6), glowMat(i % 3 ? 0x7cffd1 : 0xff6a6a, 4), Math.cos(a), Math.sin(a), 0));
    }
    engine(0, 0, -1.1, 0.16, 3.5);
  } else if (type === "shuttle") {
    g.add(turned(mesh(new THREE.CapsuleGeometry(0.5, 1.2, 6, 12), M.hull), Math.PI / 2));
    g.add(mesh(new THREE.SphereGeometry(0.42, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2), new THREE.MeshStandardMaterial({ color: 0xffd9a0, emissive: 0xc07a20, emissiveIntensity: 0.6 }), 0, 0.2, 0.6));
    g.add(mesh(new THREE.BoxGeometry(0.7, 0.5, 0.7), new THREE.MeshStandardMaterial({ color: 0x8fa8ff, flatShading: true }), 0, -0.9, 0));
    engine(0, 0, -1.2, 0.22, 4);
  } else if (type === "courier") {
    g.add(mesh(new THREE.SphereGeometry(0.55, 18, 12), M.hull));
    g.add(turned(mesh(new THREE.TorusGeometry(0.6, 0.06, 6, 24), M.dark), 0, Math.PI / 2));
    g.add(mesh(new THREE.SphereGeometry(0.28, 12, 10), glowMat(0xffd06b, 2), 0, 0, 0.38));
    engine(0, 0, -0.6, 0.14, 3);
  } else {
    g.add(mesh(new THREE.BoxGeometry(3.2, 1.0, 8), M.hull));
    g.add(mesh(new THREE.BoxGeometry(2.2, 0.8, 2), M.hull, 0, 0.6, 4.2));
    g.add(mesh(new THREE.BoxGeometry(1.0, 1.1, 1.6), M.dark, 0.9, 1.0, -1.5));
    g.add(mesh(new THREE.BoxGeometry(2.0, 0.6, 0.05), glowMat(0xffc46b, 2), 0, -0.05, 4.01));
    for (const s of [-1, 1]) g.add(mesh(new THREE.BoxGeometry(0.5, 0.4, 6), M.dark, s * 1.85, -0.2, -0.5));
    engine(-0.9, 0, -4.15, 0.42, 9);
    engine(0.9, 0, -4.15, 0.42, 9);
    engine(0, 0, -4.15, 0.5, 10);
  }
  const paint = (color: THREE.ColorRepresentation) => {
    const c = new THREE.Color(color);
    engineMats.forEach((m) => m.emissive.copy(c));
    trails.forEach((m) => m.color.copy(c));
    glows.forEach((m) => m.color.copy(c));
  };
  return { group: g, paint };
}

// ---------- things in a galaxy ----------
export function outpost(live: boolean) {
  const M = mats();
  const g = new THREE.Group();
  g.add(mesh(new THREE.CylinderGeometry(0.6, 0.8, 1.2, 12), M.hull));
  g.add(mesh(new THREE.SphereGeometry(0.6, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2), M.hull, 0, 0.6, 0));
  const w = windowsRing(0.72, 0, 16, live ? 0xffd9a0 : 0x334, 0.14, 0.12);
  g.add(w.inst);
  g.add(mesh(new THREE.CylinderGeometry(0.03, 0.03, 1, 6), M.metal, 0, 1.4, 0));
  const beacon = glowMat(live ? 0x7cffd1 : 0x223, live ? 5 : 0.2);
  g.add(mesh(new THREE.SphereGeometry(0.08, 8, 8), beacon, 0, 1.92, 0));
  const b = sprite(0x7cffd1, 1.2, live ? 0.8 : 0);
  b.position.y = 1.92;
  g.add(b);
  return { group: g, beacon: b };
}

export function pulsar(color: THREE.ColorRepresentation, ticks: Tick[], period = 4) {
  const g = new THREE.Group();
  const core = glowMat(color, 3);
  g.add(mesh(new THREE.IcosahedronGeometry(0.3, 1), core));
  const halo = sprite(color, 2, 0.6);
  g.add(halo);
  for (const s of [1, -1]) g.add(turned(mesh(new THREE.ConeGeometry(0.35, 4, 16, 1, true), additive(color, 0.18), 0, s * 2.1, 0), 0, 0, s < 0 ? Math.PI : 0));
  g.rotation.z = 0.5;
  ticks.push((t) => {
    g.rotation.y = t * 1.2;
    const flash = Math.max(0, Math.cos(((t % period) / period) * Math.PI * 2)) ** 12;
    halo.material.opacity = 0.5 + flash * 0.5;
  });
  return g;
}

export function constructionSite(building: boolean) {
  const M = mats();
  const g = new THREE.Group();
  g.add(turned(mesh(new THREE.TorusGeometry(1.6, 0.28, 8, 28, Math.PI * 1.3), M.hull), Math.PI / 2));
  const lat = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.TorusGeometry(1.6, 0.3, 4, 14, Math.PI * 0.7)),
    new THREE.LineBasicMaterial({ color: 0xffd28a, transparent: true, opacity: building ? 0.9 : 0.4 }),
  );
  lat.rotation.set(Math.PI / 2, 0, Math.PI * 1.3);
  g.add(lat);
  g.add(mesh(new THREE.CylinderGeometry(0.3, 0.3, 0.5, 10), M.dark));
  for (let i = 0; i < 3; i++) g.add(turned(mesh(new THREE.BoxGeometry(1.5, 0.06, 0.06), M.metal), 0, i * 1.4));
  if (building) {
    const sp = sprite(0xffa040, 0.8, 0.9);
    sp.position.set(Math.cos(4.2) * 1.6, 0.1, Math.sin(4.2) * 1.6);
    g.add(sp);
  }
  [0xff8a5c, 0x5fd4b0, 0xffd06b].forEach((c, i) =>
    g.add(mesh(new THREE.BoxGeometry(0.25, 0.25, 0.25), new THREE.MeshStandardMaterial({ color: c, flatShading: true }), -0.3 + i * 0.3, 0.3, 0)),
  );
  return g;
}
