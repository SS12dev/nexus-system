import base64
import os
import sys

import pytest

import nexus_workbench.app as module

linux_only = pytest.mark.skipif(sys.platform != "linux", reason="Landlock is Linux-only")


async def test_create_is_idempotent_and_starts_a_git_repo(workbench, project):
    again = await workbench.put(project)
    assert again.status_code == 200 and again.json()["created"] is False
    listed = (await workbench.get("/projects/u1")).json()
    assert [p["id"] for p in listed] == ["p1"]
    st = (await workbench.get(f"{project}/git/status")).json()
    assert st["branch"].startswith("main") and st["changes"] == ""
    assert (await workbench.get("/projects/u2")).json() == []


async def test_bad_ids_and_missing_projects(workbench):
    assert (await workbench.put("/projects/u1/Bad Name")).status_code in (400, 404)
    assert (await workbench.put("/projects/u1/..")).status_code in (400, 404)
    assert (await workbench.get("/projects/u1/nope/tree")).status_code == 404


async def test_files_persist_across_calls(workbench, project, run):
    resp = await workbench.put(f"{project}/file", json={"path": "src/app.js", "content": "a\n"})
    assert resp.status_code == 200 and resp.json()["created"]
    await run("echo 'b' >> src/app.js && mkdir -p out && echo hi > out/x.txt")
    read = (await workbench.get(f"{project}/file", params={"path": "src/app.js"})).json()
    assert read["content"] == "a\nb\n" and read["encoding"] == "text"
    tree = (await workbench.get(f"{project}/tree")).json()["entries"]
    paths = [e["path"] for e in tree]
    assert {"src/", "src/app.js", "out/x.txt", ".gitignore"} <= set(paths)
    assert not any(p.startswith((".git/", ".home/")) for p in paths)
    # A later command still sees what an earlier one made.
    assert (await run("cat out/x.txt"))["stdout"] == "hi\n"


async def test_filesystem_refusals_are_explained(workbench, project, run):
    await run("echo x > a.txt && chmod 000 a.txt")
    resp = await workbench.get(f"{project}/file", params={"path": "a.txt"})
    if os.geteuid() != 0:  # root reads anything
        assert resp.status_code == 400 and "Permission denied" in resp.json()["detail"]
    resp = await workbench.put(f"{project}/file", json={"path": "a.txt/b.txt", "content": "x"})
    assert resp.status_code == 400 and "Couldn't use a.txt/b.txt" in resp.json()["detail"]


async def test_binary_files_round_trip(workbench, project):
    data = bytes(range(256))
    await workbench.put(
        f"{project}/file",
        json={"path": "img.bin", "content": base64.b64encode(data).decode(), "encoding": "base64"},
    )
    read = (await workbench.get(f"{project}/file", params={"path": "img.bin"})).json()
    assert read["encoding"] == "base64" and base64.b64decode(read["content"]) == data


async def test_edit_replaces_exact_text(workbench, project):
    await workbench.put(f"{project}/file", json={"path": "a.txt", "content": "x = 1\ny = 1\n"})
    edit = f"{project}/edit"
    resp = await workbench.post(edit, json={"path": "a.txt", "old": "= 1", "new": "= 2"})
    assert resp.status_code == 400 and "2 times" in resp.json()["detail"]
    resp = await workbench.post(edit, json={"path": "a.txt", "old": "nope", "new": "x"})
    assert resp.status_code == 400 and "isn't in" in resp.json()["detail"]
    resp = await workbench.post(edit, json={"path": "a.txt", "old": "y = 1", "new": "y = 3"})
    assert resp.status_code == 200
    resp = await workbench.post(
        edit, json={"path": "a.txt", "old": "= ", "new": ":= ", "replace_all": True}
    )
    assert resp.json()["replaced"] == 2
    read = (await workbench.get(f"{project}/file", params={"path": "a.txt"})).json()
    assert read["content"] == "x := 1\ny := 3\n"


async def test_delete_file(workbench, project):
    await workbench.put(f"{project}/file", json={"path": "a.txt", "content": "x"})
    assert (await workbench.delete(f"{project}/file", params={"path": "a.txt"})).status_code == 200
    assert (await workbench.delete(f"{project}/file", params={"path": "a.txt"})).status_code == 404


@pytest.mark.parametrize(
    "path", ["../escape.txt", "/etc/passwd", "a/../../b", ".git/config", ".git/hooks/pre-commit"]
)
async def test_rejects_paths_outside_the_project(workbench, project, path):
    resp = await workbench.put(f"{project}/file", json={"path": path, "content": "x"})
    assert resp.status_code == 400
    resp = await workbench.get(f"{project}/file", params={"path": path})
    assert resp.status_code == 400


async def test_rejects_symlinks_that_lead_out(workbench, project, run, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    module.ROOT.joinpath("u1", "p2").mkdir(parents=True)
    os.symlink(secret, module.ROOT / "u1" / "p1" / "link.txt")
    os.symlink(tmp_path, module.ROOT / "u1" / "p1" / "outside")
    for path in ("link.txt", "outside/secret.txt"):
        resp = await workbench.get(f"{project}/file", params={"path": path})
        assert resp.status_code == 400, path
    resp = await workbench.put(f"{project}/file", json={"path": "outside/new.txt", "content": "x"})
    assert resp.status_code == 400
    assert not (tmp_path / "new.txt").exists()
    # A link inside the project is fine.
    await run("echo hi > real.txt && ln -s real.txt inner.txt")
    read = await workbench.get(f"{project}/file", params={"path": "inner.txt"})
    assert read.json()["content"] == "hi\n"


async def test_run_reports_exit_codes_and_output(run):
    result = await run("echo out; echo err >&2; exit 3")
    assert result["exit_code"] == 3 and result["stdout"] == "out\n" and "err" in result["stderr"]
    result = await run("pwd")
    assert result["stdout"].strip().endswith("/u1/p1")


async def test_timeout_kills_the_whole_process_group(run):
    result = await run("sleep 60 & sleep 60; echo never", timeout_s=1)
    assert result["timed_out"] and result["exit_code"] is None
    assert result["duration_ms"] < 10_000 and "never" not in result["stdout"]
    assert "Timed out after 1s" in result["stderr"]


async def test_background_processes_end_with_the_command(run):
    # A server left running would hold the output open; the call must still return promptly.
    result = await run("(sleep 30; echo late) & echo started")
    assert result["exit_code"] == 0 and result["stdout"] == "started\n"
    assert result["duration_ms"] < 10_000


async def test_long_output_keeps_start_and_end(run):
    result = await run("for i in $(seq 1 20000); do echo line $i; done")
    assert result["stdout"].startswith("line 1\n") and "line 20000" in result["stdout"]
    assert "characters cut" in result["stdout"]
    assert len(result["stdout"]) < module.MAX_OUTPUT_CHARS + 200


async def test_environment_is_clean(run, monkeypatch):
    monkeypatch.setenv("WORKBENCH_TOKEN", "do-not-leak")
    monkeypatch.setenv("HTTPS_PROXY", "http://egress-proxy:8888")
    result = await run("env")
    assert "do-not-leak" not in result["stdout"]
    assert "HTTPS_PROXY=http://egress-proxy:8888" in result["stdout"]
    assert "/u1/p1/.home" in result["stdout"]


async def test_git_diff_and_commit(workbench, project):
    await workbench.put(
        f"{project}/file", json={"path": "index.html", "content": "<h1>2048</h1>\n"}
    )
    diff = (await workbench.get(f"{project}/git/diff")).json()
    assert "index.html" in diff["stat"] and "+<h1>2048</h1>" in diff["diff"]
    status = (await workbench.get(f"{project}/git/status")).json()
    assert "index.html" in status["changes"]
    commit = (await workbench.post(f"{project}/git/commit", json={"message": "Add page"})).json()
    assert commit["committed"] and commit["commit"] and "1 file changed" in commit["summary"]
    again = (await workbench.post(f"{project}/git/commit", json={"message": "Again"})).json()
    assert again["committed"] is False
    assert (await workbench.get(f"{project}/git/diff")).json()["diff"] == ""
    # Diff against an earlier commit shows everything since.
    first = (await workbench.get(f"{project}/git/diff", params={"ref": "HEAD~1"})).json()
    assert "index.html" in first["stat"]
    bad = await workbench.get(f"{project}/git/diff", params={"ref": "--output=/tmp/x"})
    assert bad.status_code == 400


async def test_git_hooks_never_run(workbench, project, run, tmp_path):
    marker = module.ROOT / "u1" / "p1" / "hooked"
    await run(
        f"printf '#!/bin/sh\\ntouch {marker}\\n' > .git/hooks/pre-commit && "
        "chmod +x .git/hooks/pre-commit && echo x > a.txt"
    )
    commit = (await workbench.post(f"{project}/git/commit", json={"message": "x"})).json()
    assert commit["committed"] and not marker.exists()


async def test_delete_project(workbench, project, run):
    await run("mkdir -p ro/sub && touch ro/sub/f && chmod -R a-w ro")
    assert (await workbench.delete(project)).status_code == 200
    assert not (module.ROOT / "u1" / "p1").exists()
    assert (await workbench.delete(project)).status_code == 404


@linux_only
async def test_commands_cannot_see_other_projects(workbench, project, run):
    if not module.LANDLOCK:
        pytest.skip("this kernel has no Landlock")
    await workbench.put("/projects/u2/other")
    await workbench.put("/projects/u2/other/file", json={"path": "secret.txt", "content": "s"})
    result = await run(f"cat {module.ROOT}/u2/other/secret.txt")
    assert result["exit_code"] != 0 and "Permission denied" in result["stderr"]
    result = await run(f"ls {module.ROOT}")
    assert result["exit_code"] != 0
    # Its own folder, system tools and its temp folder all still work.
    result = await run("echo ok > mine.txt && cat mine.txt && git --version && mktemp")
    assert result["exit_code"] == 0, result["stderr"]


async def test_token_is_checked(workbench, monkeypatch):
    monkeypatch.setattr(module, "TOKEN", "s3cret")
    assert (await workbench.put("/projects/u1/p1")).status_code == 401
    resp = await workbench.put("/projects/u1/p1", headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 200
    assert (await workbench.get("/healthz")).status_code == 200


async def test_identity_sets_who_commits(workbench, project, run):
    resp = await workbench.put(
        f"{project}/git/identity",
        json={"name": "nexus[bot]", "email": "123+nexus[bot]@users.noreply.github.com"},
    )
    assert resp.status_code == 200
    await workbench.put(f"{project}/file", json={"path": "a.txt", "content": "x"})
    await workbench.post(f"{project}/git/commit", json={"message": "By the bot"})
    # Agents' own git commits get it too.
    await run("echo y > b.txt && git add b.txt && git commit -qm 'By hand'")
    log = (await run("git log -2 --format='%an <%ae>'"))["stdout"].splitlines()
    assert log == ["nexus[bot] <123+nexus[bot]@users.noreply.github.com>"] * 2
    bad = await workbench.put(f"{project}/git/identity", json={"name": "x\nevil", "email": "a@b"})
    assert bad.status_code == 422


async def test_default_identity_without_one(run):
    await run("echo y > b.txt && git add b.txt && git commit -qm 'x'")
    assert (await run("git log -1 --format=%an"))["stdout"].strip() == module.GIT_NAME


async def test_push_sends_head_to_a_branch(workbench, project, run, monkeypatch):
    # A bare repository inside the project stands in for GitHub (commands can't leave it).
    await run("git init -q --bare .home/remote.git")
    remote = str(module.ROOT / "u1" / "p1" / ".home" / "remote.git")
    monkeypatch.setattr(module, "PUSH_URL", __import__("re").compile(r"^/.*\.git$"))
    await workbench.put(f"{project}/file", json={"path": "a.txt", "content": "x"})
    await workbench.post(f"{project}/git/commit", json={"message": "Add a"})
    body = {"url": remote, "branch": "nexus/run-1", "token": "ghs_secret"}
    resp = await workbench.post(f"{project}/git/push", json=body)
    assert resp.status_code == 200, resp.text
    head = (await run("git rev-parse HEAD"))["stdout"].strip()
    assert resp.json() == {"branch": "nexus/run-1", "commit": head}
    pushed = await run("git --git-dir=.home/remote.git rev-parse refs/heads/nexus/run-1")
    assert pushed["stdout"].strip() == head
    # The token is never written into the project's git config.
    assert "ghs_secret" not in (await run("cat .git/config"))["stdout"]


@pytest.mark.parametrize(
    "url,branch",
    [
        ("https://evil.example.com/a/b.git", "main"),
        ("file:///etc/passwd.git", "main"),
        ("https://github.com/org/repo.git", "--upload-pack=x"),
        ("https://github.com/org/repo.git", "a/../b"),
    ],
)
async def test_push_only_goes_to_github(workbench, project, url, branch):
    resp = await workbench.post(
        f"{project}/git/push", json={"url": url, "branch": branch, "token": "t"}
    )
    assert resp.status_code == 400
