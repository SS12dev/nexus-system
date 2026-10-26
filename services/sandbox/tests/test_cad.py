"""The 3d-modeling skill's workflow runs in the sandbox: build123d model, check, convert, render."""

import base64
import importlib.util
import io
import json
import zipfile

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("build123d") is None,
    reason="needs the sandbox's cad extra (uv sync --all-packages --all-extras)",
)

STAND = """
from build123d import *
L, W, H, WALL = 60, 40, 20, 2.0
with BuildPart() as tray:
    Box(L, W, H, align=(Align.CENTER, Align.CENTER, Align.MIN))
    fillet(tray.edges().filter_by(Axis.Z), radius=3)
    offset(amount=-WALL, openings=tray.faces().sort_by(Axis.Z)[-1])
part = tray.part
print(f"solid volume {part.volume:.1f} mm3")
export_step(part, "out/tray.step")
export_stl(part, "out/tray.stl", tolerance=0.01, angular_tolerance=0.1)
"""

TOOL = "python skills/core/3d-modeling/scripts/model_tool.py"


def out(result, path) -> bytes:
    assert result["exit_code"] == 0, result["stdout"] + result["stderr"]
    return base64.b64decode(result["files"][path])


async def test_model_check_convert_render(run, b64, skill_files):
    files = {**skill_files("3d-modeling"), "work/tray.py": b64(STAND)}
    code = (
        "set -e\n"
        "python work/tray.py\n"
        f"{TOOL} check out/tray.step --bed 220x220x250 --one-piece --json out/check.json\n"
        f"{TOOL} convert out/tray.step out/tray.3mf\n"
        f"{TOOL} render out/tray.stl out/tray.png --views iso,top\n"
    )
    result = await run(code, files, language="bash", timeout_s=240)
    assert "RESULT: ok" in result["stdout"]
    check = json.loads(out(result, "out/check.json"))
    assert check["ok"] and check["pieces"] == 1
    assert check["size_mm"] == pytest.approx([60, 40, 20], abs=0.01)
    assert check["filament_g"] > 0
    with zipfile.ZipFile(io.BytesIO(out(result, "out/tray.3mf"))) as z:
        assert any(n.endswith(".model") for n in z.namelist())
    assert out(result, "out/tray.png")[:4] == b"\x89PNG"
    assert out(result, "out/tray.step").startswith(b"ISO-10303-21")


async def test_check_reports_a_part_too_big_for_the_bed(run, b64, skill_files):
    files = {**skill_files("3d-modeling"), "work/tray.py": b64(STAND)}
    code = f"python work/tray.py && {TOOL} check out/tray.stl --bed 50x50x50"
    result = await run(code, files, language="bash", timeout_s=240)
    assert result["exit_code"] == 1
    assert "PROBLEM" in result["stdout"] and "bed" in result["stdout"]


async def test_points_and_poles_are_not_holes(run, skill_files):
    # OpenCascade leaves sliver triangles at a cone's tip and a sphere's poles; they once made
    # check call every nose cone "not watertight, 2 pieces", so agents dropped the nose.
    code = (
        "set -e\n"
        "python -c \"from build123d import *; export_step(Cone(12, 0, 30), 'out/cone.step'); "
        "export_stl(Sphere(10), 'out/ball.stl')\"\n"
        f"{TOOL} check out/cone.step --one-piece\n"
        f"{TOOL} check out/ball.stl --one-piece\n"
    )
    result = await run(code, skill_files("3d-modeling"), language="bash", timeout_s=240)
    assert result["exit_code"] == 0, result["stdout"] + result["stderr"]
    assert result["stdout"].count("RESULT: ok") == 2


@pytest.mark.parametrize("index", [0, 1], ids=["rocket", "minion"])
async def test_the_shaped_examples_build_as_one_piece(run, b64, skill_files, index):
    files = skill_files("3d-modeling")
    doc = base64.b64decode(files["skills/core/3d-modeling/references/shapes.md"]).decode()
    example = doc.split("```python\n")[index + 1].split("```")[0]
    code = (
        "set -e\n"
        "python work/model.py\n"
        f"{TOOL} check out/*.step --one-piece\n"
        f"{TOOL} render out/*.step out/preview.png --views iso\n"
    )
    result = await run(
        code, {**files, "work/model.py": b64(example)}, language="bash", timeout_s=240
    )
    assert "RESULT: ok" in result["stdout"], result["stdout"] + result["stderr"]
    assert out(result, "out/preview.png")[:4] == b"\x89PNG"
