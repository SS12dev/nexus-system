# build123d quick reference (0.13)

`from build123d import *` gives everything below. Units are mm, angles degrees. Z is up; the
print bed is the XY plane at z = 0.

## Building a part

```python
with BuildPart() as p:
    Box(40, 20, 10)  # centred on the origin by default
    Box(40, 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))  # sits on z = 0
    Cylinder(radius=5, height=12, mode=Mode.SUBTRACT)  # cut instead of add
part = p.part
```

- Solids: `Box(l, w, h)`, `Cylinder(radius, height)`, `Sphere(radius)`,
  `Cone(bottom_radius, top_radius, height)`, `Torus(major_radius, minor_radius)`,
  `Wedge(...)`.
- `mode=Mode.ADD` (default), `Mode.SUBTRACT`, `Mode.INTERSECT` on any object.
- `align=(Align.MIN | CENTER | MAX, ..., ...)` per axis.

## Placing things

```python
with Locations((10, 0, 0), (-10, 0, 0)):  # at each point
    Cylinder(3, 10, mode=Mode.SUBTRACT)
with GridLocations(20, 15, 3, 2):  # x spacing, y spacing, x count, y count
    Hole(radius=1.7)  # through-hole down from the top
with PolarLocations(radius=15, count=6):  # around a circle
    Cylinder(2, 10)
with Locations(Location((0, 0, 5), (0, 45, 0))):  # position and rotation (x, y, z degrees)
    Box(10, 10, 2)
```

Holes: `Hole(radius, depth=None)`, `CounterBoreHole(radius, counter_bore_radius,
counter_bore_depth)`, `CounterSinkHole(radius, counter_sink_radius)`. They cut downward from
the current workplane, so put them inside `Locations` on the face you want:

```python
with Locations(p.faces().sort_by(Axis.Z)[-1]):  # the top face
    with GridLocations(30, 0, 2, 1):
        CounterSinkHole(radius=2.25, counter_sink_radius=4.5)
```

## Sketch, then extrude or revolve

```python
with BuildPart() as p:
    with BuildSketch():  # on XY; BuildSketch(Plane.XZ) for a side profile
        RectangleRounded(60, 40, 5)
        Circle(8, mode=Mode.SUBTRACT)
    extrude(amount=10)  # or extrude(amount=3, mode=Mode.SUBTRACT)
    with BuildSketch(p.faces().sort_by(Axis.Z)[-1]):  # sketch on the top face
        Text("NEXUS", font_size=8)
    extrude(amount=0.8)  # embossed lettering
```

Sketch shapes: `Rectangle`, `RectangleRounded`, `Circle`, `Ellipse`, `RegularPolygon(radius,
side_count)`, `Polygon((x, y), ...)`, `SlotOverall(width, height)`, `Text(txt, font_size)`.

Profiles from lines, then a revolve (vases, knobs, wheels):

```python
with BuildPart() as p:
    with BuildSketch(Plane.XZ):
        with BuildLine():
            Polyline((0, 0), (20, 0), (15, 30), (0, 30), close=True)
        make_face()
    revolve(axis=Axis.Z)
```

Also: `loft()` between sketches on different planes, `sweep()` along a `BuildLine` path.

## Edges and faces

```python
fillet(p.edges().filter_by(Axis.Z), radius=2)  # vertical edges
chamfer(p.edges().group_by(Axis.Z)[0], length=0.6)  # bottom edges (print-friendly)
top = p.faces().sort_by(Axis.Z)[-1]  # highest face
offset(amount=-2, openings=top)  # hollow to 2 mm walls, open at the top
```

Selectors: `.sort_by(Axis.Z)`, `.group_by(Axis.X)`, `.filter_by(Axis.Z)` (parallel to),
`.filter_by(GeomType.CIRCLE)`, `[0]` first, `[-1]` last.

## Copies and patterns of whole parts

```python
mirror(about=Plane.YZ)  # inside BuildPart: mirror what's there
half = p.part
both = half + half.mirror(Plane.YZ)  # algebra mode works on parts too
moved = part.moved(Location((50, 0, 0)))
```

## Checking and exporting

```python
part.volume, part.area  # mm3, mm2
bb = part.bounding_box()
bb.size.X, bb.min.Z
len(part.solids())  # 1 for a single piece
export_step(part, "out/x.step")
export_stl(part, "out/x.stl", tolerance=0.01, angular_tolerance=0.1)  # or model_tool convert
```

Curved profiles, sweeps along splines and worked rocket and figurine models are in
`references/shapes.md`.

## Common mistakes

- Fillet radius larger than the wall or the edge it touches: it fails or makes bad geometry.
  Fillet early edges small, or skip it.
- Two solids that only touch along a face or edge can union into a broken shape: overlap
  them by 0.01-0.1 mm.
- A part not on z = 0 prints in the air: align with `Align.MIN` on Z or `.moved()` it.
- A hole of exactly the screw size won't fit once printed: add the clearance.
