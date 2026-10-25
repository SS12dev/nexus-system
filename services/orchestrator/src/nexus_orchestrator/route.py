"""Decide, before Nexus answers, whether a message is work for an agent team. Left to itself the
chat model answers most requests on its own, research included, so the cheapest tier reads the
latest message first; when it says "team", Nexus's first move that turn is build_agent_system.
It also says whether the work is software to build, fix or extend, which gets the dev team."""

import logging
import uuid
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from nexus_orchestrator.llm import parse_json

log = logging.getLogger(__name__)

ROUTE = """You route requests for a personal assistant that orchestrates teams of \
sub-agents. A team can be a single agent; it has web search, documents, code and file tools, \
works in the background and posts the result in the chat. The user wants every real piece of \
work done by a team, so the assistant never gets stuck on a task and stays free to talk.

Answer "self" only for direct, one-step things the assistant can answer at once:
- greetings, thanks, small talk, and questions about the assistant itself (by name or not)
- a quick fact, definition or short explanation that needs no sources, in a few lines
- one look at their mail, calendar, contacts or tasks ("what's on today", "any mail from \
Alice"), or adding, moving or completing one task or event
- timers, alarms, stopwatches and reminders ("set a timer for 5 minutes", "wake me at 7", \
"remind me to call mum at 6", "remind me every day at 9 pm to ..."), and putting a clock or \
note on the screen: the assistant has its own tools for these
- changing the main screen: "show me / open the rocket model", "switch to the dashboard", \
"pin the timer first", "show the orb"
- a quick question about a document already in the chat
- orchestration: questions about a team or its result, keeping, changing, pausing or stopping \
a team, or a short follow-up on an answer the assistant just gave
- the user asks the assistant to answer itself or not to use a team

Answer "team" for everything else, which is any real work: research of any size, writing or \
drafting (emails, essays, posts, papers, outlines), analysis, planning, code, documents, decks, \
spreadsheets or charts, comparisons, summaries of long material, sending email or deleting \
things, and anything with more than one step. When unsure, answer "team".

"software" is true when the work is to build, fix or extend a program: an app, website, game, \
tool, script or bot, including a follow-up that changes one built earlier ("add a high score \
table"). It is false for explaining code, answering a programming question, or anything that \
isn't a program.

Reply with JSON only: {"route": "team"|"self", "software": bool, "reason": str}"""


@dataclass
class Route:
    team: bool = False
    # Software to build, fix or extend: the team is the dev_project team.
    software: bool = False


async def route(
    model: BaseChatModel, message: str, previous: str = "", trace: str | None = None
) -> Route:
    """Whether the latest user message should go to an agent team, and whether it is software.
    Any failure means no team: Nexus then answers as it would have without the router."""
    prompt = f"Latest message from the user:\n{message[:4000]}"
    if previous:
        prompt = f"The assistant's previous reply, for context:\n{previous[:1500]}\n\n{prompt}"
    try:
        runnable = model.bind(response_format={"type": "json_object"})
        reply = await runnable.ainvoke([SystemMessage(ROUTE), HumanMessage(prompt)])
        raw = reply.text if isinstance(reply.text, str) else reply.text()
        decision = parse_json(raw)
    except Exception:
        log.warning("Routing failed for trace %s", trace or uuid.uuid4().hex, exc_info=True)
        return Route()
    team = str(decision.get("route", "")).strip().lower() == "team"
    return Route(team=team, software=team and decision.get("software") is True)
