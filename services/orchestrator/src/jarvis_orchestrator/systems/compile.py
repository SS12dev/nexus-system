"""Compile an AgentSystemSpec into a LangGraph graph. Each agent is a subgraph (see runtime.py),
called from one node per agent; the topology decides the edges between those nodes and what
each agent is given to work on."""

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

from jarvis_orchestrator.systems.runtime import AgentState, RunContext, build_agent, final_text


def _merge(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class SystemState(TypedDict, total=False):
    # Latest result of each agent.
    outputs: Annotated[dict, _merge]
    # Conversations that continue across turns (a supervisor's lead, a critic loop's worker).
    transcripts: Annotated[dict, _merge]
    # Turns taken: per worker in supervisor_workers, review rounds in critic_loop.
    counts: Annotated[dict, _merge]
    # supervisor_workers: where the lead sent the work next.
    route: dict | None
    # parallel_map_reduce: the items and each copy's result as (index, item, result).
    items: list[str]
    results: Annotated[list, operator.add]
    final: str


# --- Terminal tools: calling one ends the agent's turn; the graph reads its arguments. -----


class _Delegate(BaseModel):
    worker: str = Field(description="Id of the worker to give the task to")
    task: str = Field(description="Self-contained instructions for the worker")


class _Finish(BaseModel):
    answer: str = Field(description="The final result for the user, complete and self-contained")


class _Items(BaseModel):
    items: list[str] = Field(description="Self-contained sub-tasks, one per item")


class _Verdict(BaseModel):
    approved: bool = Field(description="True if the work is good enough to deliver")
    feedback: str = Field(description="Specific problems and how to fix them (or why it passes)")


async def _noop(**_kwargs) -> str:
    return "Recorded."


def terminal(name: str, description: str, schema: type[BaseModel]) -> BaseTool:
    return StructuredTool.from_function(
        coroutine=_noop, name=name, description=description, args_schema=schema
    )


# --- Helpers -----------------------------------------------------------------------------


def brief(rc: RunContext, files: list[tuple[str, str]], request: str) -> str:
    spec = rc.spec
    parts = [f"<goal>\n{spec.goal}\n</goal>"]
    if request.strip() and request.strip() != spec.goal.strip():
        parts.append(f"<request>\n{request}\n</request>")
    if files:
        listing = "\n".join(f"- {name} (id {fid})" for fid, name in files)
        parts.append(f"<files>\nThe user's files for this task:\n{listing}\n</files>")
    if spec.deliverables:
        parts.append("<deliverables>\n" + "\n".join(f"- {d}" for d in spec.deliverables)
                     + "\n</deliverables>")  # fmt: skip
    return "\n\n".join(parts)


async def run_agent(rc: RunContext, graphs: dict, agent_id: str, messages: list[AnyMessage],
                    **status) -> AgentState:  # fmt: skip
    await rc.agent_status(agent_id, "running", **status)
    state = await graphs[agent_id].ainvoke(
        {"messages": messages, "steps": 0, "done": None, "escalated": False}
    )
    await rc.agent_status(agent_id, "done", **status)
    return state


def compile_system(rc: RunContext, files: list[tuple[str, str]], request: str) -> StateGraph:
    """The run's graph, not yet compiled (the runner attaches the checkpointer)."""
    spec = rc.spec
    ids = [a.id for a in spec.agents]
    task = brief(rc, files, request)
    g = StateGraph(SystemState)
    graphs: dict = {}

    match spec.topology:
        case "single":
            agent = spec.agents[0]
            graphs[agent.id] = build_agent(rc, agent)

            async def only(state: SystemState) -> dict:
                st = await run_agent(rc, graphs, agent.id, [HumanMessage(task)])
                out = final_text(st)
                return {"outputs": {agent.id: out}, "final": out}

            g.add_node(agent.id, only)
            g.add_edge(START, agent.id)
            g.add_edge(agent.id, END)

        case "pipeline":
            for a in spec.agents:
                graphs[a.id] = build_agent(rc, a)

            def stage(i: int):
                me = spec.agents[i]

                async def node(state: SystemState) -> dict:
                    prior = state.get("outputs") or {}
                    handed = "\n\n".join(
                        f'<output from="{x}">\n{prior[x]}\n</output>' for x in ids[:i] if x in prior
                    )
                    text = task + (f"\n\nWork so far:\n{handed}" if handed else "")
                    st = await run_agent(rc, graphs, me.id, [HumanMessage(text)])
                    out = final_text(st)
                    update = {"outputs": {me.id: out}}
                    if i == len(ids) - 1:
                        update["final"] = out
                    return update

                return node

            for i, a in enumerate(spec.agents):
                g.add_node(a.id, stage(i))
            g.add_edge(START, ids[0])
            for x, y in zip(ids, ids[1:], strict=False):
                g.add_edge(x, y)
            g.add_edge(ids[-1], END)

        case "supervisor_workers":
            lead, workers = spec.agents[0], spec.agents[1:]
            worker_ids = [w.id for w in workers]
            roster = "\n".join(f"- {w.id}: {w.name} ({w.role})" for w in workers)
            graphs[lead.id] = build_agent(
                rc,
                lead,
                [
                    terminal(
                        "delegate",
                        f"Give one task to a worker and wait for its result. Workers:\n{roster}",
                        _Delegate,
                    ),
                    terminal("finish", "Deliver the final result and end the run.", _Finish),
                ],
                final_hint="Call finish with the final result.",
            )
            for w in workers:
                graphs[w.id] = build_agent(rc, w)
            max_lead_turns = 2 + spec.max_rounds * len(workers)

            async def lead_node(state: SystemState) -> dict:
                counts = state.get("counts") or {}
                transcript = (state.get("transcripts") or {}).get(lead.id) or [
                    HumanMessage(f"{task}\n\nYour workers:\n{roster}")
                ]
                turns = counts.get(lead.id, 0) + 1
                if turns >= max_lead_turns:
                    transcript = [*transcript, HumanMessage(
                        "No more delegations are possible. Call finish with the final result.")]  # fmt: skip
                st = await run_agent(rc, graphs, lead.id, transcript)
                update: dict = {"transcripts": {lead.id: st["messages"]},
                                "counts": {lead.id: turns}}  # fmt: skip
                done = st.get("done") or {}
                args = done.get("args") or {}
                if done.get("tool") == "delegate" and turns < max_lead_turns:
                    worker = str(args.get("worker", "")).strip()
                    if worker in worker_ids and counts.get(worker, 0) < spec.max_rounds:
                        update["route"] = {"to": worker, "task": str(args.get("task", ""))}
                        return update
                    why = (
                        f"{worker} has had its {spec.max_rounds} tasks"
                        if worker in worker_ids
                        else f"there is no worker called {worker!r}"
                    )
                    update["transcripts"] = {lead.id: [*st["messages"], HumanMessage(
                        f"That delegation didn't happen: {why}. Delegate differently or finish.")]}  # fmt: skip
                    update["route"] = {"to": lead.id}
                    return update
                update["route"] = None
                update["final"] = final_text(st)
                return update

            def worker_node(w_id: str):
                async def node(state: SystemState) -> dict:
                    assignment = (state.get("route") or {}).get("task", "")
                    st = await run_agent(rc, graphs, w_id, [HumanMessage(
                        f"{task}\n\n<assignment from=\"{lead.id}\">\n{assignment}\n</assignment>"
                        "\n\nDo only this assignment and reply with your result.")])  # fmt: skip
                    out = final_text(st)
                    counts = state.get("counts") or {}
                    transcript = (state.get("transcripts") or {}).get(lead.id, [])
                    return {
                        "outputs": {w_id: out},
                        "counts": {w_id: counts.get(w_id, 0) + 1},
                        "transcripts": {lead.id: [*transcript, HumanMessage(
                            f'<result from="{w_id}">\n{out}\n</result>')]},  # fmt: skip
                        "route": None,
                    }

                return node

            def after_lead(state: SystemState) -> str:
                route = state.get("route")
                return route["to"] if route else END

            g.add_node(lead.id, lead_node)
            for w in worker_ids:
                g.add_node(w, worker_node(w))
                g.add_edge(w, lead.id)
            g.add_edge(START, lead.id)
            g.add_conditional_edges(lead.id, after_lead, [*worker_ids, lead.id, END])

        case "parallel_map_reduce":
            splitter, worker, combiner = spec.agents
            limit = rc.deps.settings.max_map_items
            graphs[splitter.id] = build_agent(
                rc,
                splitter,
                [terminal("submit_items", f"Hand over the sub-tasks (at most {limit}).", _Items)],
                final_hint="Call submit_items with the sub-tasks.",
            )
            graphs[worker.id] = build_agent(rc, worker)
            graphs[combiner.id] = build_agent(rc, combiner)

            async def split(state: SystemState) -> dict:
                st = await run_agent(rc, graphs, splitter.id, [HumanMessage(
                    f"{task}\n\nSplit this into at most {limit} independent sub-tasks and call "
                    "submit_items.")])  # fmt: skip
                done = st.get("done") or {}
                items = [str(x).strip() for x in (done.get("args") or {}).get("items", [])]
                items = [x for x in items if x][:limit] or [final_text(st) or rc.spec.goal]
                return {"items": items, "outputs": {splitter.id: "\n".join(items)}}

            def fan_out(state: SystemState) -> list[Send]:
                items = state.get("items") or []
                return [Send(worker.id, {"index": i, "item": it, "total": len(items)})
                        for i, it in enumerate(items)]  # fmt: skip

            async def work(payload: dict) -> dict:
                i, item, total = payload["index"], payload["item"], payload["total"]
                async with rc.parallel:
                    st = await run_agent(rc, graphs, worker.id, [HumanMessage(
                        f"{task}\n\n<item number=\"{i + 1}\" of=\"{total}\">\n{item}\n</item>\n\n"
                        "Do only this item and reply with your result.")],
                        item=f"{i + 1}/{total}")  # fmt: skip
                return {"results": [(i, item, final_text(st))]}

            async def combine(state: SystemState) -> dict:
                parts = "\n\n".join(
                    f'<result item="{i + 1}" task="{item[:200]}">\n{out}\n</result>'
                    for i, item, out in sorted(state.get("results") or [])
                )
                st = await run_agent(rc, graphs, combiner.id, [HumanMessage(
                    f"{task}\n\nResults for each part:\n\n{parts}\n\nCombine them into the final "
                    "result.")])  # fmt: skip
                out = final_text(st)
                return {"outputs": {combiner.id: out}, "final": out}

            g.add_node(splitter.id, split)
            g.add_node(worker.id, work)
            g.add_node(combiner.id, combine)
            g.add_edge(START, splitter.id)
            g.add_conditional_edges(splitter.id, fan_out, [worker.id])
            g.add_edge(worker.id, combiner.id)
            g.add_edge(combiner.id, END)

        case "critic_loop":
            doer, critic = spec.agents
            graphs[doer.id] = build_agent(rc, doer)
            graphs[critic.id] = build_agent(
                rc,
                critic,
                [terminal("verdict", "Pass the work, or send it back with feedback.", _Verdict)],
                final_hint="Call verdict.",
            )

            async def do(state: SystemState) -> dict:
                transcript = (state.get("transcripts") or {}).get(doer.id) or [HumanMessage(task)]
                st = await run_agent(rc, graphs, doer.id, transcript)
                return {"transcripts": {doer.id: st["messages"]},
                        "outputs": {doer.id: final_text(st)}}  # fmt: skip

            async def review(state: SystemState) -> dict:
                rounds = (state.get("counts") or {}).get(critic.id, 0) + 1
                work = (state.get("outputs") or {}).get(doer.id, "")
                st = await run_agent(rc, graphs, critic.id, [HumanMessage(
                    f"{task}\n\n<work round=\"{rounds}\" by=\"{doer.id}\">\n{work}\n</work>\n\n"
                    "Review it against the goal and call verdict.")])  # fmt: skip
                done = st.get("done") or {}
                args = done.get("args") or {}
                approved = bool(args.get("approved", done.get("tool") != "verdict"))
                feedback = str(args.get("feedback") or final_text(st))
                update: dict = {"counts": {critic.id: rounds}, "outputs": {critic.id: feedback}}
                if approved or rounds >= spec.max_rounds:
                    update["final"] = work
                    update["route"] = None
                else:
                    transcript = (state.get("transcripts") or {}).get(doer.id, [])
                    update["transcripts"] = {doer.id: [*transcript, HumanMessage(
                        f"<review by=\"{critic.id}\">\n{feedback}\n</review>\n\nRevise your work "
                        "to address this and reply with the full revised result.")]}  # fmt: skip
                    update["route"] = {"to": doer.id}
                return update

            def after_review(state: SystemState) -> str:
                return doer.id if state.get("route") else END

            g.add_node(doer.id, do)
            g.add_node(critic.id, review)
            g.add_edge(START, doer.id)
            g.add_edge(doer.id, critic.id)
            g.add_conditional_edges(critic.id, after_review, [doer.id, END])

    return g
