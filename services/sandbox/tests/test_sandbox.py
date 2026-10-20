import base64


async def test_runs_python_and_returns_out_files(run, b64):
    result = await run(
        "import pathlib\nprint(open('in/a.txt').read().upper())\n"
        "pathlib.Path('out/b.txt').write_text('done')\npathlib.Path('scratch.txt').write_text('x')",
        {"in/a.txt": b64("hello")},
    )
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "HELLO"
    # Only out/ comes back.
    assert list(result["files"]) == ["out/b.txt"]
    assert base64.b64decode(result["files"]["out/b.txt"]) == b"done"


async def test_bash_and_errors(run):
    result = await run("echo hi; ls in out; exit 3", language="bash")
    assert result["exit_code"] == 3 and "hi" in result["stdout"]
    result = await run("raise ValueError('boom')")
    assert result["exit_code"] != 0 and "ValueError: boom" in result["stderr"]


async def test_timeout_kills_the_job(run):
    result = await run("import time\ntime.sleep(30)", timeout_s=1)
    assert result["timed_out"] and result["exit_code"] is None
    assert result["duration_ms"] < 10_000


async def test_environment_is_clean(run, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "do-not-leak")
    result = await run("import os\nprint(sorted(os.environ))")
    assert "SECRET_KEY" not in result["stdout"]


async def test_rejects_paths_outside_the_job(sandbox, b64):
    resp = await sandbox.post(
        "/run", json={"code": "print(1)", "files": {"../escape.txt": b64("x")}}
    )
    assert resp.status_code == 400


async def test_token_is_checked(sandbox, monkeypatch):
    import jarvis_sandbox.app as module

    monkeypatch.setattr(module, "TOKEN", "s3cret")
    resp = await sandbox.post("/run", json={"code": "print(1)"})
    assert resp.status_code == 401
    resp = await sandbox.post(
        "/run", json={"code": "print(1)"}, headers={"Authorization": "Bearer s3cret"}
    )
    assert resp.status_code == 200
