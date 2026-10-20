"""Decide, before Jarvis answers, whether a message is work for an agent team. Left to itself the
chat model answers most requests on its own, research included, so the cheapest tier reads the
latest message first; when it says "team", Jarvis's first move that turn is build_agent_system."""

import logging
import uuid

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from jarvis_orchestrator.llm import parse_json

log = logging.getLogger(__name__)

ROUTE = """You route requests for Jarvis, a personal assistant that can either answer itself or \
start a team of sub-agents (with web search, documents, code and file tools) that works in the \
background and posts the result in the chat.

Answer "team" when the request needs real work done:
- research across several sources: literature reviews, surveys, research gaps, market or \
product comparisons, "find the best X", anything for a paper, report or decision
- making deliverables: reports, documents, decks, spreadsheets, charts
- the same job for many items, or several steps that depend on each other
- sending email or deleting things (only agents in a team can do that)
- the user asks for a team or agents

Answer "self" for everything else: greetings and chat, quick facts and explanations, \
brainstorming or advice, questions about their mail, calendar, contacts or tasks, adding a task \
or event, questions about their documents, follow-ups on an answer or a team's result, and \
whenever the user asks Jarvis to answer itself or not to use a team.

Reply with JSON only: {"route": "team"|"self", "reason": str}"""


async def wants_team(
    model: BaseChatModel, message: str, previous: str = "", trace: str | None = None
) -> bool:
    """True when the latest user message should go to an agent team. Any failure means no:
    Jarvis then answers as it would have without the router."""
    prompt = f"Latest message from the user:\n{message[:4000]}"
    if previous:
        prompt = f"Jarvis's previous reply, for context:\n{previous[:1500]}\n\n{prompt}"
    try:
        runnable = model.bind(response_format={"type": "json_object"})
        reply = await runnable.ainvoke([SystemMessage(ROUTE), HumanMessage(prompt)])
        raw = reply.text if isinstance(reply.text, str) else reply.text()
        decision = parse_json(raw)
    except Exception:
        log.warning("Routing failed for trace %s", trace or uuid.uuid4().hex, exc_info=True)
        return False
    return str(decision.get("route", "")).strip().lower() == "team"
