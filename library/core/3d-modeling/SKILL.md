---
name: 3d-modeling
description: Design 3D-printable parts and models - brackets, enclosures, mounts, gears, and stylised vehicles, figurines and characters - as parametric build123d code, exported as STEP, STL and 3MF, checked for printability and visually reviewed, with previews and a filament estimate. Use for any 3D model, CAD or 3D printing request.
metadata:
  category: engineering
---

# 3D modeling for printing

You write the part as Python with **build123d** (exact CAD on the OpenCascade kernel), run it
with `run_code`, check and render it with this skill's script, then **look at the render**
with `view_image` and fix what's wrong before you report. Everything runs in the sandbox, in
millimetres. The user gets an editable STEP, a print-ready STL and 3MF, preview images, the
code, and a filament estimate.

Two kinds of request:

- **Functional parts** (a bracket, a box, a mount): exact dimensions matter. Sections 1 to 3.
- **Shaped models** (a rocket, a car, a figurine, a character): it must be recognisable.
  Load `references/shapes.md` too: list the features that make the thing recognisable, build
  one smooth main form (a revolved profile, a loft) and add the features onto it. A crude
  stack of boxes, cylinders and balls is a failure, not a stylised version. These come out
  stylised, like a toy or a game piece, not sculpted: say that once, without apologising.
  Faces, realistic animals and portraits are beyond code-CAD: offer a stylised version, or a
  sculpting or text-to-3D tool.

## 1. Pin down the part before modelling

- Before starting over, look in `in/` and the library (`list_documents`, if you have it) for
  an earlier version of the same model: its saved `.py` is the starting point for a change
  ("make the fins bigger", "another minion but taller") and saves a full rebuild.
- Sizes that matter: what it holds or fits (measure the device, the screw, the shelf), and
  clearances. Ask for the one or two you can't sensibly guess; assume the rest and say so.
- The user's printer: look in memory for its build volume and nozzle (e.g. "Bambu A1, 256 mm
  bed"). If unknown, assume 220 x 220 x 250 mm and a 0.4 mm nozzle, and say so.
- Material if it matters (PLA default; PETG for outdoors or heat; TPU for flexible).
- A model they attached is in `in/`. Run `check` on it first to learn its size and state. A
  STEP opens in build123d (`import_step("in/x.step")`) and can be cut, extended and
  re-exported. A mesh (STL, 3MF) can be scaled, checked, split or converted, but not edited
  like CAD: offer to remodel it as code when they want real changes.

## 2. Write it as parameters

Start the code with every size as a named constant, then build from them, so "make it 5 mm
wider" is a one-line change. Load `references/build123d.md` for the API: it lists what works
in this version. Keep it to one `BuildPart` per printed piece.

Print rules to design in, not fix later:

| Rule | Value |
| --- | --- |
| Walls | at least 1.2 mm (3 lines at 0.4 mm); 2 mm or more for strength |
| Holes and fits | +0.2-0.4 mm clearance; screw holes: M3 3.4 mm, M4 4.5 mm, M5 5.5 mm |
| Overhangs | at most 45 degrees from vertical, or chamfer them; bridges under about 20 mm |
| Base | a flat face on the bed, as large as possible; put the part on z = 0 |
| Corners | fillet or chamfer outside corners (0.5-2 mm); chamfer bottom edges rather than fillet |
| Size | split anything bigger than the bed into parts that join (pins, dovetails, screws) |
| Text | embossed or engraved at least 0.6 mm deep and 5 mm tall |

## 3. Export, check, render, look

Save the script itself to `out/` and run the whole pipeline in one `run_code` call (bash),
so the user gets the code and later changes start from it:

```bash
mkdir -p out && cat > out/stand.py <<'PY'
from build123d import *

# ... parameters, then build `part` ...
print(f"solid volume {part.volume:.1f} mm3, {len(part.solids())} solid(s)")
export_step(part, "out/stand.step")
PY
python out/stand.py
T=skills/core/3d-modeling/scripts/model_tool.py
python $T check out/stand.step --bed 256x256x256 --one-piece --json out/stand-check.json
python $T convert out/stand.step out/stand.stl
python $T convert out/stand.step out/stand.3mf
python $T render out/stand.stl out/stand.png
```

Each call starts fresh and has about two minutes. If a big model runs out of time, build in
one call and check in the next: files saved by earlier calls are in `in/` (`in/stand.step`).

`convert` writes a clean mesh (welded, no sliver triangles); prefer it to `export_stl`.

- `check` exits 1 with `PROBLEM:` lines when the part must be fixed: not watertight, no
  volume, the mesh volume differs from the CAD solid's (a boolean silently dropped or doubled
  a piece; renders often hide this), it doesn't fit the bed, or it is in pieces with
  `--one-piece`. `note:` lines are warnings to weigh: thin features, little flat base, a lot
  of overhang. Fix problems and run it again; **never report a part that fails `check`**.
- `--material` (PLA, PETG, ABS, ASA, TPU, NYLON), `--infill` and `--price-per-kg` shape the
  filament estimate. It is a rough, slicer-free figure: say so.
- `render` draws iso, front, side and top views (`--views` to choose; `back` too). The front
  view looks at the -Y side.
- **Then call `view_image` on the PNG and actually look.** Go through what was asked (every
  feature on your list, the proportions, which way it faces, nothing floating or sunk out of
  sight) and say to yourself what you see, not what you meant to build. Fix and look again;
  two or three rounds is normal for a shaped model. Never describe a feature in your reply
  that the render doesn't show. Without `view_image`, reason from the check numbers and the
  bounding box of each feature, and say the model wasn't visually checked.

## 4. Hand it over

Everything in `out/` is saved to the user's library: the STEP (edit in Fusion, FreeCAD,
Onshape), STL and 3MF (slice in Bambu Studio, PrusaSlicer, Cura, OrcaSlicer), the PNG preview
and the script. Call `present_result` with the STL so the model opens on the user's screen.
In the reply give: what was made and its key dimensions, the assumptions you made, how to
print it (orientation, supports or not, material, about how many grams), and what's easy to
change. For a change request, edit the parameters in the saved script rather than starting
over.

If the CAD library itself keeps failing (an abort, a crash, a timeout on a simple shape), stop
and say so plainly. Never write placeholder or empty STEP, STL or 3MF files to stand in for a
model, and list only files that `ls -l out/` shows with a real size.
