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


def build_graph(model: BaseChatModel, tools: Sequence[BaseTool] = ()):
    bound = model.bind_tools(list(tools)) if tools else model

    async def respond(state: JarvisState) -> dict:
        reply = await bound.ainvoke([SystemMessage(state["system_prompt"]), *state["messages"]])
        return {"messages": [reply]}

    graph = StateGraph(JarvisState)
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    if tools:
        graph.add_node("tools", ToolNode(list(tools)))
        graph.add_conditional_edges("respond", tools_condition, {"tools": "tools", END: END})
        graph.add_edge("tools", "respond")
    else:
        graph.add_edge("respond", END)
    return graph.compile()
