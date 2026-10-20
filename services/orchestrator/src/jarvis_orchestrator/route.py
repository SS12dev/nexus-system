"""Decide, before Jarvis answers, whether a message is work for an agent team. Left to itself the
chat model answers most requests on its own, research included, so the cheapest tier reads the
latest message first; when it says "team", Jarvis's first move that turn is build_agent_system."""

import logging
import uuid

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from jarvis_orchestrator.llm import parse_json

log = logging.getLogger(__name__)

ROUTE = """You route requests for Jarvis, a personal assistant that orchestrates teams of \
sub-agents. A team can be a single agent; it has web search, documents, code and file tools, \
works in the background and posts the result in the chat. The user wants every real piece of \
work done by a team, so Jarvis never gets stuck on a task and stays free to talk.

Answer "self" only for direct, one-step things Jarvis can answer at once:
- greetings, thanks, small talk, and questions about Jarvis itself
- a quick fact, definition or short explanation that needs no sources, in a few lines
- one look at their mail, calendar, contacts or tasks ("what's on today", "any mail from \
Alice"), or adding, moving or completing one task or event
- a quick question about a document already in the chat
- orchestration: questions about a team or its result, keeping, changing, pausing or stopping \
a team, or a short follow-up on an answer Jarvis just gave
- the user asks Jarvis to answer itself or not to use a team

Answer "team" for everything else, which is any real work: research of any size, writing or \
drafting (emails, essays, posts, papers, outlines), analysis, planning, code, documents, decks, \
spreadsheets or charts, comparisons, summaries of long material, sending email or deleting \
things, and anything with more than one step. When unsure, answer "team".

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
