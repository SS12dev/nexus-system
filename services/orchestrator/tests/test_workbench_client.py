"""Deleting a missing workspace must not use the client's ensure-and-recreate path."""

import httpx

from nexus_orchestrator.workbench import WorkbenchClient


async def test_delete_project_is_direct_and_idempotent(settings):
    requests = []

    def respond(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(404, json={"detail": "No such project."})

    client = httpx.AsyncClient(
        base_url=settings.workbench_url,
        transport=httpx.MockTransport(respond),
    )
    workbench = WorkbenchClient(settings, client)
    workbench._ready.add(("user-id", "project-id"))

    await workbench.delete_project("user-id", "project-id")

    assert requests == [("DELETE", "/projects/user-id/project-id")]
    assert ("user-id", "project-id") not in workbench._ready
    await client.aclose()
