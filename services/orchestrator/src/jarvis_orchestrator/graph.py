"""The Jarvis graph. Phase 1 has a single respond node; routing, tools and agent systems are
added as further nodes in later phases."""

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph


class JarvisState(MessagesState):
    system_prompt: str


def build_graph(model: BaseChatModel):
    async def respond(state: JarvisState) -> dict:
        reply = await model.ainvoke([SystemMessage(state["system_prompt"]), *state["messages"]])
        return {"messages": [reply]}

    graph = StateGraph(JarvisState)
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    graph.add_edge("respond", END)
    return graph.compile()
