"""The Jarvis graph. Phase 2 has a respond node that can call Jarvis's own tools (documents and
memory) in a loop; routing and agent systems are added as further nodes in phase 3."""

from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

# Model calls plus tool rounds allowed in one turn before Jarvis gives up.
RECURSION_LIMIT = 16


class JarvisState(MessagesState):
    system_prompt: str
    # A tool Jarvis must call on its first model call of the turn (the router sent the
    # request to a team); cleared once it has.
    force_tool: str | None


def build_graph(model: BaseChatModel, tools: Sequence[BaseTool] = ()):
    tools = list(tools)
    bound = model.bind_tools(tools) if tools else model
    names = {t.name for t in tools}

    async def respond(state: JarvisState) -> dict:
        forced = state.get("force_tool")
        runnable = (
            model.bind_tools(tools, tool_choice=forced) if forced and forced in names else bound
        )
        reply = await runnable.ainvoke([SystemMessage(state["system_prompt"]), *state["messages"]])
        return {"messages": [reply], "force_tool": None}

    graph = StateGraph(JarvisState)
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    if tools:
        graph.add_node("tools", ToolNode(tools))
        graph.add_conditional_edges("respond", tools_condition, {"tools": "tools", END: END})
        graph.add_edge("tools", "respond")
    else:
        graph.add_edge("respond", END)
    return graph.compile()
