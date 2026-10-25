"""save_widget: a widget-builder team's HTML is checked before it reaches the screen, lands on
the dock as a custom widget, and each change is a version the user can undo."""

import re

from sqlalchemy import select

from nexus_common.models import CustomWidget, Widget
from nexus_orchestrator.tools import ToolContext, build_tools

GOOD = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<div id="n">3 days</div><script>nexus.state.get().then(() => {});</script></body></html>"""


def tools_for(deps, user, chat_id):
    async def emit(type_, **data):
        pass

    ctx = ToolContext(deps=deps, user_id=user.id, chat_id=chat_id, emit=emit)
    return {t.name: t for t in build_tools(ctx, ["save_widget", "control_widget", "list_widgets"])}


async def test_bad_html_is_sent_back_with_reasons(deps, owner):
    user, chat_id = owner
    tools = tools_for(deps, user, chat_id)
    html = '<html><head><script src="https://cdn.x/y.js"></script></head><body><div></div>'
    html += "<script>fetch('https://api.x');localStorage.a=1</script></body></html>"
    out = await tools["save_widget"].ainvoke({"name": "Bad", "html": html})
    assert out.startswith("Not saved")
    assert "loads a script from a URL" in out and "nexus.fetch" in out and "nexus.state" in out
    out = await tools["save_widget"].ainvoke({"name": "X", "html": GOOD, "data": ["passwords"]})
    assert "No data source passwords" in out


async def test_a_widget_lands_on_the_dock_and_changes_can_be_undone(deps, owner):
    user, chat_id = owner
    tools = tools_for(deps, user, chat_id)
    out = await tools["save_widget"].ainvoke(
        {"name": "Launch", "html": GOOD, "size": "s", "hosts": ["https://ll.thespacedevs.com/x"]}
    )
    assert (
        "Saved Launch" in out
        and "version 1" in out
        and "Allow button for ll.thespacedevs.com" in out
    )
    wid = re.search(r"id ([0-9a-f-]{36})", out).group(1)

    out = await tools["save_widget"].ainvoke(
        {"name": "Launch v2", "html": GOOD.replace("3 days", "2 days"), "widget_id": wid}
    )
    assert "version 2" in out
    async with deps.sessionmaker() as db:
        w = await db.scalar(select(Widget).where(Widget.kind == "custom"))
        rows = (await db.scalars(select(CustomWidget).order_by(CustomWidget.version))).all()
    assert [r.version for r in rows] == [1, 2] and rows[0].root_id == rows[1].root_id
    assert w.state["custom_id"] == str(rows[1].id) and w.title == "Launch v2"
    assert "version 2 of 2" in await tools["list_widgets"].ainvoke({})

    out = await tools["control_widget"].ainvoke({"widget_id": wid, "action": "undo"})
    assert out.startswith("Done")
    async with deps.sessionmaker() as db:
        w = await db.get(Widget, w.id)
    assert w.state["version"] == 1 and w.title == "Launch"
    out = await tools["control_widget"].ainvoke({"widget_id": wid, "action": "undo"})
    assert "first version" in out
