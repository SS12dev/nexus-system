import { Bounds, Grid, OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { useEffect, useMemo, useState } from "react";
import * as THREE from "three";
import { modelFormat, type ModelFormat } from "../lib/preview";

/** Turns the file into a three.js object. STL, 3MF, OBJ and PLY are Z-up like slicers; GLB is
 * Y-up like the web. Everything comes back Y-up, standing on y = 0, centred on x and z. */
async function load(blob: Blob, format: ModelFormat): Promise<THREE.Object3D> {
  const material = new THREE.MeshStandardMaterial({ color: "#7dd3fc", roughness: 0.55, metalness: 0.1 });
  let obj: THREE.Object3D;
  let zUp = true;
  if (format === "stl") {
    const { STLLoader } = await import("three/examples/jsm/loaders/STLLoader.js");
    obj = new THREE.Mesh(new STLLoader().parse(await blob.arrayBuffer()), material);
  } else if (format === "ply") {
    const { PLYLoader } = await import("three/examples/jsm/loaders/PLYLoader.js");
    const geometry = new PLYLoader().parse(await blob.arrayBuffer());
    geometry.computeVertexNormals();
    obj = new THREE.Mesh(geometry, material);
  } else if (format === "3mf") {
    const { ThreeMFLoader } = await import("three/examples/jsm/loaders/3MFLoader.js");
    obj = new ThreeMFLoader().parse(await blob.arrayBuffer());
  } else if (format === "obj") {
    const { OBJLoader } = await import("three/examples/jsm/loaders/OBJLoader.js");
    obj = new OBJLoader().parse(await blob.text());
  } else {
    const { GLTFLoader } = await import("three/examples/jsm/loaders/GLTFLoader.js");
    const gltf = await new GLTFLoader().parseAsync(await blob.arrayBuffer(), "");
    obj = gltf.scene;
    zUp = false;
  }
  // Print files rarely carry useful colour: one material, so every part looks the same.
  if (format !== "glb")
    obj.traverse((o) => {
      if (o instanceof THREE.Mesh) o.material = material;
    });
  const root = new THREE.Group();
  if (zUp) obj.rotation.x = -Math.PI / 2;
  root.add(obj);
  root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(root);
  const centre = box.getCenter(new THREE.Vector3());
  obj.position.sub(new THREE.Vector3(centre.x, box.min.y, centre.z));
  return root;
}

interface Stats {
  size: [number, number, number];
  triangles: number;
}

function stats(obj: THREE.Object3D): Stats {
  obj.updateMatrixWorld(true);
  const s = new THREE.Box3().setFromObject(obj).getSize(new THREE.Vector3());
  let triangles = 0;
  obj.traverse((o) => {
    if (o instanceof THREE.Mesh) {
      const g = o.geometry as THREE.BufferGeometry;
      triangles += (g.index ? g.index.count : g.attributes.position.count) / 3;
    }
  });
  // Width x depth x height in the file's units (mm for print files): y is up here.
  return { size: [s.x, s.z, s.y], triangles };
}

const round = (n: number) => (n >= 100 ? n.toFixed(0) : n.toFixed(1));

/** An orbitable view of a 3D model on a print-bed grid, with its size. */
export default function ModelView({ blob, name }: { blob: Blob; name: string }) {
  const format = modelFormat(name);
  const [obj, setObj] = useState<THREE.Object3D | null>(null);
  const [failed, setFailed] = useState(false);
  const [wire, setWire] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setObj(null);
    setFailed(false);
    if (!format) return setFailed(true);
    load(blob, format)
      .then((o) => !cancelled && setObj(o))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [blob, format]);

  useEffect(() => {
    obj?.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        for (const m of Array.isArray(o.material) ? o.material : [o.material])
          if ("wireframe" in m) (m as THREE.MeshStandardMaterial).wireframe = wire;
      }
    });
  }, [obj, wire]);

  const info = useMemo(() => (obj ? stats(obj) : null), [obj]);
  const span = info ? Math.max(...info.size) : 100;

  if (failed)
    return <p className="p-5 text-sm text-rose-400">Couldn't show this model. Download it to open in a slicer.</p>;
  if (!obj) return <p className="p-5 text-sm text-slate-500">Loading the model…</p>;

  return (
    <div className="relative min-h-0 flex-1" data-theme="dark">
      <Canvas
        camera={{ position: [span, span * 0.8, span * 1.2], fov: 40, near: span / 1000, far: span * 100 }}
        className="bg-[#0b1020]"
        aria-label={`3D view of ${name}`}
      >
        <hemisphereLight args={["#ffffff", "#334155", 1.1]} />
        <directionalLight position={[1, 2, 1.5]} intensity={1.6} />
        <directionalLight position={[-1.5, 0.5, -1]} intensity={0.5} />
        <Bounds fit clip observe margin={1.3}>
          <primitive object={obj} />
        </Bounds>
        <Grid
          args={[span * 4, span * 4]}
          cellSize={span > 200 ? 10 : span > 40 ? 5 : 1}
          sectionSize={span > 200 ? 100 : span > 40 ? 50 : 10}
          cellColor="#1e293b"
          sectionColor="#334155"
          fadeDistance={span * 6}
          infiniteGrid
        />
        <OrbitControls makeDefault enableDamping />
      </Canvas>
      <div className="pointer-events-none absolute top-3 left-3 rounded-md bg-black/50 px-2.5 py-1.5 text-xs text-slate-200">
        {info && (
          <>
            {info.size.map(round).join(" × ")} mm · {Math.round(info.triangles).toLocaleString()} triangles
          </>
        )}
      </div>
      <div className="absolute top-3 right-3 flex gap-1">
        <button
          onClick={() => setWire((w) => !w)}
          aria-pressed={wire}
          className={`rounded-md px-2.5 py-1 text-xs ${wire ? "bg-sky-600 text-white" : "bg-black/50 text-slate-200 hover:bg-black/70"}`}
        >
          Wireframe
        </button>
      </div>
      <p className="pointer-events-none absolute bottom-3 left-3 text-xs text-slate-400">
        Drag to turn · scroll to zoom · right-drag to move
      </p>
    </div>
  );
}
