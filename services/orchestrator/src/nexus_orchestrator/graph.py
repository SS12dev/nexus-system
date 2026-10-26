"""The Nexus graph. Phase 2 has a respond node that can call Nexus's own tools (documents and
memory) in a loop; routing and agent systems are added as further nodes in phase 3."""

from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from nexus_orchestrator.tools import ToolContext, older_images_as_text, show_images

# Model calls plus tool rounds allowed in one turn before Nexus gives up.
RECURSION_LIMIT = 16


class AssistantState(MessagesState):
    system_prompt: str
    # A tool Nexus must call on its first model call of the turn (the router sent the
    # request to a team); cleared once it has.
    force_tool: str | None


def build_graph(
    model: BaseChatModel, tools: Sequence[BaseTool] = (), ctx: ToolContext | None = None
):
    """ctx, when given, is the tools' context: pictures view_image fetched through it are shown
    to the model after the tool round."""
    tools = list(tools)
    bound = model.bind_tools(tools) if tools else model
    names = {t.name for t in tools}

    async def respond(state: AssistantState) -> dict:
        forced = state.get("force_tool")
        runnable = (
            model.bind_tools(tools, tool_choice=forced) if forced and forced in names else bound
        )
        messages = older_images_as_text(state["messages"])
        reply = await runnable.ainvoke([SystemMessage(state["system_prompt"]), *messages])
        return {"messages": [reply], "force_tool": None}

    graph = StateGraph(AssistantState)
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    if tools:
        tool_node = ToolNode(tools)

        async def run_tools(state: AssistantState, config: RunnableConfig) -> dict:
            out = (await tool_node.ainvoke(state, config))["messages"]
            return {"messages": [*out, *show_images(ctx, out)]}

        graph.add_node("tools", run_tools)
        graph.add_conditional_edges("respond", tools_condition, {"tools": "tools", END: END})
        graph.add_edge("tools", "respond")
    else:
        graph.add_edge("respond", END)
    return graph.compile()
