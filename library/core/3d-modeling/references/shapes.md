# Shaped models: vehicles, figurines, characters

Code-CAD makes good stylised models of things people recognise (a rocket, a car, a minion, a
robot, a chess piece) when they are built from smooth, joined forms rather than a pile of
boxes and balls. Two worked examples follow; both pass `check` as one piece. Copy the
approach, not the numbers.

## Method

1. **Write down what makes it recognisable** before any code: the 5 to 8 features someone
   would name, each with its size relative to the overall height and where it sits. A
   minion: capsule body about 1.6 times as tall as wide; one or two big goggles at about 70%
   height on a strap round the head; overalls on the lower third with a front pocket; short
   arms with round hands; stubby legs and shoes; a few hairs. A rocket: a long tube; a pointed
   ogive nose about a third of the body's length; three or four swept fins at the base; a
   flared engine skirt; a porthole. If you don't know what the thing looks like, look it up
   with `web_search` first.
2. **Build the big form first, as one smooth solid.** Anything round about an axis (a body, a
   nose cone, a vase, a bottle, a chess piece) is one `revolve` of a profile with lines,
   arcs and splines. Don't stack a cylinder, a cone and a sphere: the seams show and the
   proportions drift.
3. **Add the features on top, each overlapping what it joins by 0.5 mm or more** so the union
   is one solid: tubes `sweep`-ed along a spline for arms, handles and pipes; thin polygons
   extruded `both=True` for fins and wings; rims as a cylinder with a smaller one cut out;
   grooves (a smile, panel lines) cut 1 to 2 mm deep.
4. **Put the front on -Y** (the render's front view looks at it) and the model on z = 0 on a
   flat base or feet.
5. **Look at it** (render, then `view_image`), compare with the list from step 1, and fix
   what is missing, misplaced or out of proportion.

## A rocket

```python
import math

from build123d import *

# Every size is a parameter (mm).
BODY_R, BODY_H = 12, 70  # main tube
NOSE_H = 34  # tangent-ogive nose cone
SKIRT_R, SKIRT_H = 14, 8  # flared engine skirt at the base
FINS, FIN_SPAN, FIN_ROOT, FIN_TIP, FIN_T = 3, 16, 30, 12, 2.4
WINDOW_R = 4.5


def ogive(r, h, n=24):
    """(radius, height) points of a tangent ogive from its base (r, 0) to its tip (0, h)."""
    rho = (r * r + h * h) / (2 * r)
    return [(math.sqrt(rho * rho - z * z) + r - rho, z) for z in (h * i / n for i in range(n + 1))]


top = SKIRT_H + BODY_H
profile = [(0, 0), (SKIRT_R, 0), (BODY_R, SKIRT_H), (BODY_R, top)]
profile += [(x, top + z) for x, z in ogive(BODY_R, NOSE_H)[1:]]

with BuildPart() as rocket:
    # The whole round body is one revolved profile: smooth and joined, not stacked primitives.
    with BuildSketch(Plane.XZ):
        with BuildLine():
            Polyline(*profile, close=True)
        make_face()
    revolve(axis=Axis.Z)
    # Swept-back fins, sunk 1 mm into the body so they fuse with it.
    for i in range(FINS):
        with BuildSketch(Plane.XZ.rotated((0, 0, 360 / FINS * i))):
            Polygon(
                (BODY_R - 1, 0),
                (BODY_R + FIN_SPAN, 0),
                (BODY_R + FIN_SPAN, FIN_TIP),
                (BODY_R - 1, FIN_ROOT),
                align=None,
            )
        extrude(amount=FIN_T / 2, both=True)
    # A porthole between two fins: a raised rim with a recessed glass.
    turn = Rotation(0, 0, 180 / FINS)  # half-way between the first two fins
    at = Location((0, 0, SKIRT_H + 0.7 * BODY_H)) * turn * Location((BODY_R, 0, 0))
    with Locations(at * Rotation(0, 90, 0)):
        Cylinder(WINDOW_R + 1.2, 1.6)
        Cylinder(WINDOW_R, 2.4, mode=Mode.SUBTRACT)
        Cylinder(WINDOW_R, 0.6, align=(Align.CENTER, Align.CENTER, Align.MAX))

part = rocket.part
print(f"solid volume {part.volume:.1f} mm3, {len(part.solids())} solid(s)")
export_step(part, "out/rocket.step")
```

## A minion figurine

```python
from build123d import *

# A stylised, recognisable minion figurine (mm). Proportions from the character: a capsule
# body about 1.6x as tall as it is wide, one big goggle at about 70% height on a strap,
# overalls on the lower third, short legs and arms.
R = 15  # body radius
H = 50  # body height including both domes
GOGGLE_Z = 0.68 * H
GOGGLE_R, RIM_W, RIM_D = 7.5, 2.2, 4
STRAP_W, STRAP_T = 4.5, 1.2
OVERALL_Z, OVERALL_T = 0.36 * H, 0.8
LEG_R, LEG_H, FOOT = 4.2, 5, (10, 8, 3.5)
ARM_R = 2.4

with BuildPart() as minion:
    # Body: one revolved capsule profile (round top, softly rounded bottom), raised on the legs.
    base = LEG_H + FOOT[2]
    with BuildSketch(Plane.XZ):
        with BuildLine():
            l1 = Line((0, base), (R - 4, base))
            a1 = JernArc(l1 @ 1, (1, 0), 4, 90)  # rounded bottom edge
            l2 = Line(a1 @ 1, (R, base + H - R))
            a2 = JernArc(l2 @ 1, (0, 1), R, 90)  # hemispherical top
            Line(a2 @ 1, (0, base))
        make_face()
    revolve(axis=Axis.Z)
    gz = base + GOGGLE_Z
    # Goggle strap: a band round the head, slightly proud of the body.
    with Locations((0, 0, gz)):
        Cylinder(R + STRAP_T, STRAP_W)
    # Goggle: a thick rim with a recessed lens, on the front (-Y).
    with Locations(Location((0, -R + 1, gz), (90, 0, 0))):
        Cylinder(GOGGLE_R + RIM_W, RIM_D, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, 0, RIM_D)):
            Cylinder(GOGGLE_R, 2, mode=Mode.SUBTRACT)
            Sphere(GOGGLE_R * 0.55)  # the eye
    # Smile: a shallow curved groove below the goggle.
    with BuildSketch(Plane.XZ.offset(R + 2)):
        with Locations((0, gz - 12)):
            Ellipse(6, 2.5)
        with Locations((0, gz - 10.8)):
            Ellipse(6.6, 2.5, mode=Mode.SUBTRACT)
    extrude(amount=-4.5, mode=Mode.SUBTRACT)  # back into the body
    # Overalls: a thin shell round the lower body plus a bib pocket on the front.
    with Locations((0, 0, base)):
        Cylinder(R + OVERALL_T, OVERALL_Z, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with Locations((0, -R - OVERALL_T, base + OVERALL_Z - 5)):
        Box(9, 1.6, 6)  # front pocket
    # Arms: tubes swept down along the sides, with round hands.
    for side in (-1, 1):
        with BuildLine() as path:
            Spline(
                (side * (R - 2), 0, base + 0.5 * H),
                (side * (R + 4), -1, base + 0.36 * H),
                (side * (R + 5), -3, base + 0.22 * H),
            )
        with BuildSketch(Plane(path.line @ 0, z_dir=path.line % 0)):
            Circle(ARM_R)
        sweep(path=path.line)
        with Locations(path.line @ 1):
            Sphere(ARM_R * 1.5)
        # Legs and shoes.
        with Locations((side * 6, 0, FOOT[2])):
            Cylinder(LEG_R, LEG_H + 1, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((side * 6, -1.5, 0)):
            Box(*FOOT, align=(Align.CENTER, Align.CENTER, Align.MIN))
    # Hair: a few thin strands on top.
    for x, tilt in ((-3, -20), (0, 0), (3, 20)):
        with Locations(Location((x, 0, base + H - 1), (0, tilt, 0))):
            Cylinder(0.7, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))

part = minion.part
print(f"solid volume {part.volume:.1f} mm3, {len(part.solids())} solid(s)")
export_step(part, "out/minion.step")
```

## Recipes

- Rounded profile corner in a `BuildLine`: `JernArc(start_point, start_tangent, radius,
  arc_degrees)`; `l @ 1` is the end point of line `l`, `l % 1` its direction there.
- Smooth curve through points: `Spline((x, y), ..., (x, y))` in a sketch, or with 3D points in
  a `BuildLine` used as a sweep path.
- Profile for a sweep: `BuildSketch(Plane(path @ 0, z_dir=path % 0))` puts the sketch square
  to the path's start.
- A feature on a round surface at angle `a` around Z, facing out:
  `Location((0, 0, z)) * Rotation(0, 0, a) * Location((R, 0, 0)) * Rotation(0, 90, 0)`.
- Organic blobs: overlap a few `Sphere`s or scaled ellipsoids
  (`scale(Sphere(r), by=(1, 0.8, 1.3))`), then `fillet` the joins if it works; `loft()`
  between `Ellipse` sketches at several heights makes a smooth tapering body.
- Too fine to print: details under about 1 mm (thin hair, small lettering) break off or
  vanish. Make them 1.5 mm or more, or leave them out and say so.
