"""What one agent-system run needs while it executes: the run's key and budget, model-tier
policy, prompts, tools, live status for the UI, approvals and pause/cancel.

Each agent is its own small LangGraph graph (model -> tools -> model ...). Risky tool calls are
approved before anything in that batch runs: the tools node records an approval row, then
calls LangGraph's interrupt(), which ends the job with the run's state checkpointed in Postgres.
When the user decides, a new job resumes the graph and the node reads the decision back.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Annotated, TypedDict

import openai
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from sqlalchemy import select

from nexus_common.events import Event, publish
from nexus_common.models import (
    AgentRun,
    AgentSystem,
    Approval,
    ApprovalStatus,
    Chat,
    Message,
    MessageRole,
    MessageStatus,
    RunEvent,
    RunStatus,
    SystemStatus,
    User,
    utcnow,
)
from nexus_common.spec import TIER_MODEL, AgentSpec, AgentSystemSpec, tier_down, tier_up
from nexus_common.systems import approval_payload, money
from nexus_common.toolinfo import BY_NAME, needs_approval
from nexus_orchestrator.tools import ToolContext, build_tools

log = logging.getLogger(__name__)

TOOL_OUTPUT_CHARS = 12000


class RunCancelled(Exception):
    pass


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int
    # Set when the agent called one of its terminal tools (delegate, finish, submit_items,
    # verdict): {"tool": name, "args": {...}}.
    done: dict | None
    # Retried one tier up after an empty answer or a model error.
    escalated: bool


def text_of(message: AnyMessage) -> str:
    t = message.text
    return t if isinstance(t, str) else t()


@dataclass
class RunContext:
    deps: object  # TurnDeps
    run_id: uuid.UUID
    system_id: uuid.UUID
    user: User
    chat_id: uuid.UUID | None
    spec: AgentSystemSpec
    trace_id: str
    budget_usd: float
    api_key: str
    # Tools this user may use; the spec's tools are intersected with it.
    allowed: set[str]
    allow_max: bool
    file_ids: list[uuid.UUID] = field(default_factory=list)
    prompts: dict[str, str] = field(default_factory=dict)
    tool_contexts: dict[str, ToolContext] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _parallel: asyncio.Semaphore | None = None

    @property
    def project(self) -> str:
        """The workbench project this run's ws_* tools work in."""
        return self.spec.project or str(self.run_id)

    @property
    def parallel(self) -> asyncio.Semaphore:
        if self._parallel is None:
            self._parallel = asyncio.Semaphore(self.deps.settings.max_parallel_agents)
        return self._parallel

    # --- Events --------------------------------------------------------------------------

    async def publish(self, type_: str, **data) -> None:
        payload = {"system_id": str(self.system_id), "run_id": str(self.run_id), **data}
        await publish(
            self.deps.redis, self.user.id, Event(type=type_, chat_id=self.chat_id, data=payload)
        )

    async def record(self, type_: str, agent: str | None, **data) -> None:
        async with self.deps.sessionmaker() as db:
            db.add(
                RunEvent(
                    run_id=self.run_id, user_id=self.user.id, type=type_, agent=agent, data=data
                )
            )
            await db.commit()

    async def post(self, content: str) -> None:
        """Say something in the run's chat before the final result (a dev team's plan)."""
        if self.chat_id is None:
            return
        # turn imports the systems package, so import it here rather than at the top.
        from nexus_orchestrator.turn import message_payload

        async with self.deps.sessionmaker() as db:
            chat = await db.get(Chat, self.chat_id)
            if chat is None:
                return
            msg = Message(
                chat_id=chat.id,
                role=MessageRole.assistant,
                content=content,
                status=MessageStatus.complete,
                model=None,
                trace_id=self.trace_id,
            )
            db.add(msg)
            chat.updated_at = utcnow()
            await db.commit()
            payload = {**message_payload(msg), "attachments": []}
        await publish(
            self.deps.redis,
            self.user.id,
            Event(type="message.completed", chat_id=self.chat_id, data={"message": payload}),
        )

    async def pause(self) -> None:
        """Park the run as if the user had pressed pause: the caller then interrupts with kind
        "pause", and resuming the run (the user saying go) carries on from there."""
        async with self._lock, self.deps.sessionmaker() as db:
            run = await db.get(AgentRun, self.run_id)
            system = await db.get(AgentSystem, run.system_id)
            run.status = RunStatus.paused
            system.status = SystemStatus.paused
            await db.commit()

    async def emit_tool(self, agent: str, type_: str, **data) -> None:
        """Tool events from an agent's ToolContext: shown live and kept for the timeline."""
        await self.publish(type_, agent=agent, **data)
        if type_ == "tool.call":
            await self.record("tool.call", agent, **data)

    async def agent_status(self, agent: str, status: str, **extra) -> None:
        async with self._lock, self.deps.sessionmaker() as db:
            run = await db.get(AgentRun, self.run_id)
            agents = dict(run.agents or {})
            cur = dict(agents.get(agent, {}))
            cur.update(status=status, **extra)
            agents[agent] = cur
            run.agents = agents
            await db.commit()
        await self.publish("agent.status", agent=agent, status=status, **extra)
        if status in ("running", "done", "failed"):
            await self.record("agent.status", agent, status=status, **extra)

    async def add_usage(self, usage: dict | None) -> None:
        if not usage:
            return
        async with self._lock, self.deps.sessionmaker() as db:
            run = await db.get(AgentRun, self.run_id)
            run.input_tokens += usage.get("input_tokens", 0)
            run.output_tokens += usage.get("output_tokens", 0)
            run.steps += 1
            await db.commit()

    # --- Control and budget ----------------------------------------------------------------

    async def status(self) -> RunStatus:
        async with self.deps.sessionmaker() as db:
            return await db.scalar(select(AgentRun.status).where(AgentRun.id == self.run_id))

    async def checkpoint_control(self) -> None:
        """Between model calls: stop if cancelled, park the run if the user paused it."""
        st = await self.status()
        if st == RunStatus.cancelled:
            raise RunCancelled
        if st == RunStatus.paused:
            interrupt({"kind": "pause"})

    async def spent(self) -> float:
        if self.deps.settings.litellm_virtual_keys and self.api_key:
            value = await self.deps.gateway.spend(self.api_key)
            if value is not None:
                async with self.deps.sessionmaker() as db:
                    run = await db.get(AgentRun, self.run_id)
                    run.cost_usd = money(value)
                    await db.commit()
                return float(value)
        return 0.0

    async def tier_for(self, agent: AgentSpec, escalated: bool) -> str:
        """The spec's tier, adjusted by policy: jarvis-max only for the lead when the user's role
        allows it; one tier up after a failure; non-lead agents one tier down once less than
        20% of the run's budget is left."""
        is_lead = agent.id == self.spec.lead.id
        tier = agent.tier
        if tier == "max" and not (is_lead and self.allow_max):
            tier = "pro"
        if escalated:
            tier = self.escalate(agent, tier)
        if not is_lead and self.budget_usd > 0:
            spent = await self.spent()
            if spent >= 0.8 * self.budget_usd:
                tier = tier_down(tier)
        return tier

    def escalate(self, agent: AgentSpec, tier: str) -> str:
        up = tier_up(tier)
        if up == "max" and not (agent.id == self.spec.lead.id and self.allow_max):
            return tier
        return up

    def model(self, agent: AgentSpec, tier: str):
        return self.deps.model_factory(
            model=TIER_MODEL[tier],
            api_key=self.api_key,
            metadata={
                "trace_id": self.trace_id,
                "trace_name": "nexus.system",
                "generation_name": f"agent.{agent.id}",
                "session_id": str(self.chat_id) if self.chat_id else None,
                "trace_user_id": str(self.user.id),
                "tags": ["jarvis", "system", self.deps.settings.env],
                "trace_metadata": {
                    "system_id": str(self.system_id),
                    "run_id": str(self.run_id),
                    "agent": agent.id,
                    "tier": tier,
                },
            },
        )

    # --- Tools and approvals -----------------------------------------------------------------

    def tools_for(self, agent: AgentSpec) -> list[BaseTool]:
        async def emit(type_: str, **data) -> None:
            await self.emit_tool(agent.id, type_, **data)

        ctx = self.tool_contexts.get(agent.id)
        if ctx is None:
            ctx = ToolContext(
                deps=self.deps,
                user_id=self.user.id,
                chat_id=self.chat_id,
                emit=emit,
                run_id=self.run_id,
                agent=agent.id,
                skills=set(agent.skills),
                file_ids=list(self.file_ids),
                project=self.project,
            )
            self.tool_contexts[agent.id] = ctx
        names = [t for t in agent.tools if t in self.allowed and t in BY_NAME]
        return build_tools(ctx, names)

    async def approval_for(self, agent: str, call: dict) -> Approval:
        """The approval row for a tool call, created (and announced) the first time."""
        async with self.deps.sessionmaker() as db:
            a = await db.scalar(
                select(Approval).where(
                    Approval.run_id == self.run_id, Approval.tool_call_id == call["id"]
                )
            )
            if a is not None:
                return a
            info = BY_NAME.get(call["name"])
            reason = (
                f"{info.title}: {info.description}" if info else f"{call['name']} needs approval"
            )
            a = Approval(
                run_id=self.run_id,
                system_id=self.system_id,
                user_id=self.user.id,
                chat_id=self.chat_id,
                agent=agent,
                tool=call["name"],
                tool_call_id=call["id"],
                args=call.get("args") or {},
                reason=reason,
                status=ApprovalStatus.pending,
            )
            db.add(a)
            await db.commit()
        await self.publish("approval.requested", approval=approval_payload(a))
        await self.record("approval.requested", agent, tool=a.tool, approval_id=str(a.id))
        await self.agent_status(agent, "waiting", tool=a.tool)
        return a

    async def reload_approval(self, approval_id: uuid.UUID) -> Approval:
        async with self.deps.sessionmaker() as db:
            return await db.get(Approval, approval_id)


def _is_budget_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return isinstance(exc, openai.APIStatusError) and ("budget" in text or "exceeded" in text)


def build_agent(
    rc: RunContext,
    agent: AgentSpec,
    terminal: list[BaseTool] | None = None,
    final_hint: str = "",
):
    """One agent as a graph: model -> tools -> model ... until it answers, calls a terminal
    tool, or reaches its step limit."""
    terminal = terminal or []
    terminal_names = {t.name for t in terminal}
    tools = rc.tools_for(agent)
    by_name = {t.name: t for t in tools}
    max_steps = agent.max_steps or rc.deps.settings.agent_max_steps
    approval_extra = set(rc.spec.approval.required_for)

    async def model_node(state: AgentState) -> dict:
        await rc.checkpoint_control()
        steps = state.get("steps", 0)
        last = steps + 1 >= max_steps
        tier = await rc.tier_for(agent, state.get("escalated", False))
        available = terminal if last else [*tools, *terminal]
        messages = [SystemMessage(rc.prompts[agent.id]), *state["messages"]]
        if last:
            messages.append(
                HumanMessage(
                    "You have reached your step limit. Stop using tools and give your final "
                    f"result now.{(' ' + final_hint) if final_hint else ''}"
                )
            )
        await rc.agent_status(agent.id, "thinking", tier=tier, steps=steps + 1)
        reply = None
        for attempt in (0, 1):
            llm = rc.model(agent, tier)
            bound = llm.bind_tools(available) if available else llm
            try:
                reply = await bound.ainvoke(messages)
                break
            except openai.APIStatusError as exc:
                # Budget errors end the run; other model errors get one retry a tier up.
                if _is_budget_error(exc) or attempt == 1:
                    raise
                log.warning("Agent %s: %s, retrying one tier up", agent.id, exc)
                tier = rc.escalate(agent, tier)
        await rc.add_usage(reply.usage_metadata)
        return {"messages": [reply], "steps": steps + 1}

    async def tools_node(state: AgentState) -> dict:
        ai: AIMessage = state["messages"][-1]
        calls = ai.tool_calls
        # Approvals first, so nothing in this batch runs before the user has decided.
        # Every approval in the batch is recorded before the run parks, so the user sees them
        # all at once; the run resumes once none is pending.
        decided: dict[str, Approval] = {}
        for call in calls:
            if call["name"] in by_name and needs_approval(call["name"], approval_extra):
                decided[call["id"]] = await rc.approval_for(agent.id, call)
        for cid, a in decided.items():
            while a.status == ApprovalStatus.pending:
                interrupt({"kind": "approval", "approval_id": str(a.id)})
                a = await rc.reload_approval(a.id)
            decided[cid] = a
        out: list[ToolMessage] = []
        done = None
        for call in calls:
            name, cid = call["name"], call["id"]
            if name in terminal_names:
                done = {"tool": name, "args": call.get("args") or {}}
                out.append(ToolMessage("Recorded.", tool_call_id=cid, name=name))
                continue
            tool = by_name.get(name)
            if tool is None:
                out.append(
                    ToolMessage(
                        f"You don't have a tool called {name}.",
                        tool_call_id=cid,
                        name=name,
                        status="error",
                    )
                )
                continue
            a = decided.get(cid)
            if a is not None and a.status == ApprovalStatus.rejected:
                note = f" Their note: {a.note}" if a.note else ""
                out.append(
                    ToolMessage(
                        f"The user rejected this {name} call.{note} Adjust your plan accordingly.",
                        tool_call_id=cid,
                        name=name,
                    )
                )
                continue
            args = (a.decided_args if a is not None and a.decided_args else None) or call["args"]
            await rc.agent_status(agent.id, "working", tool=name)
            try:
                result = await tool.ainvoke(args)
            except Exception as exc:
                log.warning("Tool %s failed for agent %s: %s", name, agent.id, exc)
                result = f"Tool error: {exc.__class__.__name__}: {str(exc)[:500]}"
            text = str(result)
            if len(text) > TOOL_OUTPUT_CHARS:
                text = text[:TOOL_OUTPUT_CHARS] + f"\n… [{len(text) - TOOL_OUTPUT_CHARS} chars cut]"
            if a is not None and a.decided_args:
                text = f"(The user edited the arguments before approving.)\n{text}"
            out.append(ToolMessage(text, tool_call_id=cid, name=name))
        return {"messages": out, "done": done}

    def after_model(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        if not text_of(last).strip() and not state.get("escalated"):
            return "nudge"
        return END

    async def nudge(state: AgentState) -> dict:
        return {
            "messages": [HumanMessage("You ended without an answer. Give your final result.")],
            "escalated": True,
        }

    def after_tools(state: AgentState) -> str:
        return END if state.get("done") else "model"

    g = StateGraph(AgentState)
    g.add_node("model", model_node)
    g.add_node("tools", tools_node)
    g.add_node("nudge", nudge)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", after_model, {"tools": "tools", "nudge": "nudge", END: END})
    g.add_edge("nudge", "model")
    g.add_conditional_edges("tools", after_tools, {"model": "model", END: END})
    return g.compile()


def final_text(state: AgentState) -> str:
    """An agent's result: its terminal tool's main argument, else its last reply."""
    done = state.get("done")
    if done:
        args = done.get("args") or {}
        for key in ("answer", "result", "summary", "feedback", "task"):
            if isinstance(args.get(key), str) and args[key].strip():
                return args[key]
    for m in reversed(state.get("messages", [])):
        if isinstance(m, AIMessage) and text_of(m).strip():
            return text_of(m)
    return ""
