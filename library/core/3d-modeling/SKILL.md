---
name: 3d-modeling
description: Design 3D-printable parts and CAD models from a description - brackets, enclosures, mounts, organisers, adapters, gears - as parametric build123d code, exported as STEP, STL and 3MF, checked for printability, with preview images and a filament estimate. Use for any 3D model, CAD or 3D printing request.
metadata:
  category: engineering
---

# 3D modeling for printing

You write the part as Python with **build123d** (exact CAD on the OpenCascade kernel), run it
with `run_code`, then check and render it with this skill's script. Everything runs in the
sandbox, in millimetres. The user gets an editable STEP, a print-ready STL and 3MF, preview
images, the code, and a filament estimate.

This is for functional, dimensioned parts. Sculpted or organic shapes (figurines, faces,
animals) are not what code-CAD does well: say so, and offer a stylised version built from
simple shapes, or suggest a dedicated sculpting or text-to-3D tool.

## 1. Pin down the part before modelling

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

## 3. Export, check, render

In the same `run_code` call as the model, export and print the solid's volume:

```python
from build123d import *

# ... build `part` ...
print(f"solid volume {part.volume:.1f} mm3")
export_step(part, "out/stand.step")
export_stl(part, "out/stand.stl", tolerance=0.01, angular_tolerance=0.1)
```

Then check and render (bash in `run_code`, or `subprocess` from the same Python). Each
`run_code` call has about two minutes, so build in one call and check in the next:

```bash
python skills/core/3d-modeling/scripts/model_tool.py check out/stand.step --bed 256x256x256 --one-piece --json out/stand-check.json
python skills/core/3d-modeling/scripts/model_tool.py convert out/stand.step out/stand.3mf
python skills/core/3d-modeling/scripts/model_tool.py render out/stand.stl out/stand.png
```

- `check` exits 1 with `PROBLEM:` lines when the part must be fixed: not watertight, no
  volume, the mesh volume differs from the CAD solid's (a boolean silently dropped or doubled
  a piece; renders often hide this), it doesn't fit the bed, or it is in pieces with
  `--one-piece`. `note:` lines are warnings to weigh: thin features, little flat base, a lot
  of overhang. Fix problems and run it again; **never report a part that fails `check`**.
- `--material` (PLA, PETG, ABS, ASA, TPU, NYLON), `--infill` and `--price-per-kg` shape the
  filament estimate. It is a rough, slicer-free figure: say so.
- `render` draws iso, front, side and top views (`--views` to choose). Look at the numbers
  as well as the picture: size, piece count, volume.

## 4. Hand it over

Everything in `out/` is saved to the user's library: the STEP (edit in Fusion, FreeCAD,
Onshape), STL and 3MF (slice in Bambu Studio, PrusaSlicer, Cura, OrcaSlicer), the PNG preview,
and save the script too (`out/stand.py`) so it can be changed later. In the reply give: what
was made and its key dimensions, the assumptions you made, how to print it (orientation,
supports or not, material, about how many grams), and what's easy to change. For a change
request, edit the parameters in the saved script rather than starting over.

If the CAD library itself keeps failing (an abort, a crash, a timeout on a simple shape), stop
and say so plainly. Never write placeholder or empty STEP, STL or 3MF files to stand in for a
model, and list only files that `ls -l out/` shows with a real size.
