#!/usr/bin/env python3
"""Check, render and convert 3D models for printing.

  check   MODEL [--bed 256x256x256] [--expect-volume MM3] [--material PLA] [--infill 15]
          Is it printable? Watertight, consistent, one piece, fits the bed, matches the
          volume the CAD reported, how much overhangs, and roughly how much filament.
          Prints a report and exits 1 when something must be fixed.
  render  MODEL OUT.png [--views iso,front,side,top]
          Shaded views side by side, for the user and for checking the shape.
  convert MODEL OUT    (.stl, .3mf, .glb, .obj; from STEP too)

MODEL is .stl, .3mf, .obj, .glb/.gltf or .step/.stp (read with build123d). Units: mm.
"""

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path

DENSITY = {"PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "ASA": 1.07, "TPU": 1.21, "NYLON": 1.14}
FILAMENT_MM = 1.75
OVERHANG_DEG = 45
VOLUME_TOLERANCE = 0.02
MIN_FEATURE_MM = 1.2


def load(path: str):
    """A trimesh mesh, and the exact solid volume when the source is a STEP file."""
    import trimesh

    p = Path(path)
    exact = None
    if p.suffix.lower() in (".step", ".stp"):
        from build123d import export_stl, import_step

        shape = import_step(str(p))
        exact = float(shape.volume)
        with tempfile.TemporaryDirectory() as tmp:
            stl = Path(tmp) / "m.stl"
            export_stl(shape, str(stl), tolerance=0.01, angular_tolerance=0.1)
            mesh = trimesh.load(str(stl), force="mesh")
    else:
        mesh = trimesh.load(str(p), force="mesh")
    if mesh.is_empty:
        raise SystemExit(f"{path}: no geometry in this file")
    return mesh, exact


def parse_bed(text: str) -> tuple[float, float, float]:
    parts = [float(x) for x in text.lower().replace("×", "x").split("x")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("give the bed as WxDxH in mm, e.g. 256x256x256")
    return parts[0], parts[1], parts[2]


def check(args) -> int:
    import numpy as np

    mesh, exact = load(args.model)
    problems: list[str] = []
    notes: list[str] = []
    size = [round(float(x), 2) for x in mesh.extents]
    bodies = len(mesh.split(only_watertight=False))
    volume = float(mesh.volume) if mesh.is_volume else float("nan")

    if not mesh.is_watertight:
        problems.append("not watertight: the surface has holes, so slicers may print it wrong")
    if not mesh.is_winding_consistent:
        problems.append("inconsistent face winding: some faces point inward")
    if not (volume > 0):
        problems.append("no positive volume: this is a surface, not a solid")
    expect = args.expect_volume if args.expect_volume is not None else exact
    if expect and volume > 0 and abs(volume - expect) / expect > VOLUME_TOLERANCE:
        problems.append(
            f"volume {volume:.0f} mm3 differs from the expected {expect:.0f} mm3 by more than "
            f"{VOLUME_TOLERANCE:.0%}: a boolean probably dropped or doubled a piece"
        )
    if bodies > 1:
        (problems if args.one_piece else notes).append(
            f"{bodies} separate pieces "
            "(fine for a multi-part print, wrong if they should be joined)"
        )
    if args.bed:
        bed = sorted(args.bed[:2]) + [args.bed[2]]
        footprint = sorted(size[:2]) + [size[2]]
        if any(f > b for f, b in zip(footprint, bed, strict=True)):
            problems.append(
                f"{size[0]} x {size[1]} x {size[2]} mm does not fit the {args.bed[0]:g} x "
                f"{args.bed[1]:g} x {args.bed[2]:g} mm bed: split it or scale it"
            )
    thin = [axis for axis, v in zip("XYZ", size, strict=True) if v < MIN_FEATURE_MM]
    if thin:
        notes.append(f"under {MIN_FEATURE_MM} mm along {', '.join(thin)}: too thin to print well")

    # Downward-facing area: on the bed it's the base, above it it's overhang.
    normals, areas = mesh.face_normals, mesh.area_faces
    zmin = float(mesh.bounds[0][2])
    centres = mesh.triangles_center[:, 2]
    down = normals[:, 2] < -math.cos(math.radians(OVERHANG_DEG))
    on_bed = centres < zmin + 0.05
    base = float(areas[down & on_bed].sum())
    overhang = float(areas[down & ~on_bed].sum())
    total = float(areas.sum()) or 1.0
    footprint_area = size[0] * size[1] or 1.0
    if base < 0.05 * footprint_area:
        notes.append("little flat area on the bed: it may tip or need a brim; consider rotating it")
    if overhang / total > 0.1:
        notes.append(
            f"{overhang / total:.0%} of the surface overhangs more than {OVERHANG_DEG} degrees: "
            "expect supports, or reorient or chamfer those faces"
        )

    report = {
        "file": args.model,
        "size_mm": size,
        "volume_mm3": round(volume, 1) if volume == volume else None,
        "exact_volume_mm3": round(exact, 1) if exact else None,
        "surface_mm2": round(total, 1),
        "watertight": bool(mesh.is_watertight),
        "pieces": bodies,
        "base_area_mm2": round(base, 1),
        "overhang_share": round(overhang / total, 3),
        "triangles": int(len(mesh.faces)),
    }
    if volume > 0:
        # Walls are solid; the inside is infill. A rough, slicer-free estimate.
        walls = min(volume, total * args.walls * args.nozzle)
        used = walls + (volume - walls) * args.infill / 100
        density = DENSITY.get(args.material.upper(), 1.24)
        grams = used / 1000 * density
        metres = used / (math.pi * (FILAMENT_MM / 2) ** 2) / 1000
        report.update(
            material=args.material.upper(),
            infill_percent=args.infill,
            filament_g=round(grams, 1),
            filament_m=round(metres, 2),
        )
        if args.price_per_kg:
            report["filament_cost"] = round(grams / 1000 * args.price_per_kg, 2)
    report["problems"] = problems
    report["notes"] = notes
    report["ok"] = not problems

    print(f"{Path(args.model).name}: {size[0]} x {size[1]} x {size[2]} mm, {bodies} piece(s)")
    if volume > 0:
        print(f"volume {volume:.0f} mm3" + (f" (CAD solid {exact:.0f} mm3)" if exact else ""))
        print(
            f"about {report['filament_g']} g / {report['filament_m']} m of "
            f"{report['material']} at {args.infill}% infill"
            + (f", cost {report['filament_cost']}" if "filament_cost" in report else "")
        )
    for p in problems:
        print(f"PROBLEM: {p}")
    for n in notes:
        print(f"note: {n}")
    print("RESULT: ok" if not problems else "RESULT: fix the problems above")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=2))
    del np
    return 0 if not problems else 1


VIEWS = {"iso": (24, -58), "front": (0, -90), "side": (0, 0), "top": (90, -90), "back": (0, 90)}


def render(args) -> int:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    mesh, _ = load(args.model)
    views = [v.strip() for v in args.views.split(",") if v.strip() in VIEWS] or ["iso"]
    light = np.array([0.35, -0.55, 0.75])
    light /= np.linalg.norm(light)
    shade = 0.3 + 0.7 * np.clip(mesh.face_normals @ light, 0, 1)
    colors = plt.cm.Blues(0.35 + 0.6 * shade)
    lo, hi = mesh.bounds
    centre, radius = (lo + hi) / 2, float((hi - lo).max()) / 2 or 1.0
    fig = plt.figure(figsize=(3.4 * len(views), 3.6), dpi=args.dpi)
    for i, name in enumerate(views):
        ax = fig.add_subplot(1, len(views), i + 1, projection="3d")
        ax.add_collection3d(Poly3DCollection(mesh.triangles, facecolors=colors, edgecolor="none"))
        for set_lim, c in zip((ax.set_xlim, ax.set_ylim, ax.set_zlim), centre, strict=True):
            set_lim(c - radius, c + radius)
        ax.view_init(*VIEWS[name])
        ax.set_box_aspect((1, 1, 1), zoom=1.3)
        ax.set_axis_off()
        ax.set_title(name, fontsize=10, color="#334155")
    size = " x ".join(f"{x:.1f}" for x in mesh.extents)
    fig.suptitle(f"{Path(args.model).name}  ·  {size} mm", fontsize=10, color="#0f172a")
    fig.tight_layout()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, facecolor="white")
    print(f"wrote {args.out}")
    return 0


def convert(args) -> int:
    out = Path(args.out)
    if out.suffix.lower() not in (".stl", ".3mf", ".glb", ".obj", ".ply"):
        raise SystemExit("convert to .stl, .3mf, .glb, .obj or .ply")
    mesh, _ = load(args.model)
    out.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(str(out))
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="is it printable, and how much filament")
    c.add_argument("model")
    c.add_argument("--bed", type=parse_bed, help="printer build volume WxDxH in mm")
    c.add_argument("--expect-volume", type=float, help="the CAD solid's volume in mm3")
    c.add_argument("--one-piece", action="store_true", help="separate pieces are a problem")
    c.add_argument("--material", default="PLA", help=f"one of {', '.join(DENSITY)}")
    c.add_argument("--infill", type=float, default=15, help="infill percent (default 15)")
    c.add_argument("--walls", type=int, default=2, help="perimeters (default 2)")
    c.add_argument("--nozzle", type=float, default=0.4, help="nozzle mm (default 0.4)")
    c.add_argument("--price-per-kg", type=float, help="filament price per kg, for a cost")
    c.add_argument("--json", help="also write the report as JSON here")
    r = sub.add_parser("render", help="shaded views as a PNG")
    r.add_argument("model")
    r.add_argument("out")
    r.add_argument("--views", default="iso,front,side,top", help=f"any of {', '.join(VIEWS)}")
    r.add_argument("--dpi", type=int, default=110)
    v = sub.add_parser("convert", help="write another mesh format")
    v.add_argument("model")
    v.add_argument("out")
    args = ap.parse_args()
    return {"check": check, "render": render, "convert": convert}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
