"""Prompts for agent systems: each agent's system prompt, and the design prompt Jarvis uses to
write an AgentSystemSpec."""

from datetime import UTC, datetime

from jarvis_common.models import Skill
from jarvis_common.skills import Layers, catalogue
from jarvis_common.spec import AgentSpec, AgentSystemSpec
from jarvis_common.toolinfo import ToolInfo, needs_approval


def position(spec: AgentSystemSpec, agent: AgentSpec) -> str:
    ids = [a.id for a in spec.agents]
    i = ids.index(agent.id)
    names = {a.id: f"{a.name} ({a.id})" for a in spec.agents}
    match spec.topology:
        case "single":
            return "You do the whole task yourself."
        case "pipeline":
            before = "You get the results of the earlier steps. " if i else ""
            after = (
                f"Your result goes to {names[ids[i + 1]]}, the next step."
                if i + 1 < len(ids)
                else "Your result is the final answer the user sees."
            )
            return f"You are step {i + 1} of {len(ids)} in a pipeline. {before}{after}"
        case "supervisor_workers":
            if i == 0:
                return (
                    "You lead the team. Plan the work, give one well-specified task at a time to "
                    "a worker with `delegate` and read their result, which comes back to you. "
                    "Do small parts yourself. When the goal is met, call `finish` with the "
                    "complete final result (it is what the user sees)."
                )
            return (
                f"You are a worker for {names[ids[0]]}, who gives you one assignment at a time. "
                "Do exactly that assignment and reply with your result."
            )
        case "parallel_map_reduce":
            return [
                "You split the task into independent sub-tasks of similar size, each "
                "self-contained, and hand them over with `submit_items`.",
                "You handle one sub-task; copies of you handle the others in parallel. Reply with "
                "your result for your item only.",
                "You combine the results of all sub-tasks into the final result the user sees: "
                "merge, de-duplicate and resolve conflicts.",
            ][i]
        case "critic_loop":
            if i == 0:
                return (
                    f"You do the work. {names[ids[1]]} reviews it and may send it back with "
                    "feedback; then revise and reply with the full revised result."
                )
            return (
                f"You review the work of {names[ids[0]]} against the goal and call `verdict`: "
                "approve work that is good enough, or give specific feedback to fix."
            )
    return ""


def agent_prompt(
    spec: AgentSystemSpec,
    agent: AgentSpec,
    layers: Layers,
    skills: list[Skill],
    tools: list[ToolInfo],
    user_name: str | None,
    assistant: str,
) -> str:
    today = datetime.now(UTC).strftime("%A %d %B %Y")
    parts = [layers.render()] if layers.render() else []
    parts.append(
        f"<you>\nYou are {agent.name}, the {agent.role} in a team of agents {assistant} built for "
        f"{user_name or 'the user'} to do this: {spec.name}.\n{position(spec, agent)}\n</you>"
    )
    parts.append(f"<instructions>\n{agent.instructions}\n</instructions>")
    if skills:
        parts.append(
            "<skills>\nSkills granted to you. When your task matches one, call skills_load with "
            f"its id first.\n{catalogue(skills)}\nskills_search finds other enabled skills.\n"
            "</skills>"
        )
    if tools:
        listing = "\n".join(f"- {t.name}: {t.description}" for t in tools)
        gated = [t.name for t in tools if needs_approval(t.name, set(spec.approval.required_for))]
        if gated:
            listing += (
                f"\n\nCalling {', '.join(gated)} pauses the run and asks the user to approve, "
                "edit or reject that exact call in the app; that is the approval step. When your "
                "task includes it, call it with the final arguments: don't wait for approval, "
                "ask for it in your reply, or skip it. If the user rejects it, you are told so; "
                "then don't retry it and say so in your result."
            )
        parts.append(f"<tools>\n{listing}\n</tools>")
    parts.append(
        "<working>\n"
        f"- Today is {today}.\n"
        "- Every model and tool call costs the user money; work efficiently and stop when done.\n"
        "- Your final reply is your result and is passed on as it is: make it complete and "
        "self-contained. The user reads the final result in the chat, not in files, so it must "
        "contain the actual answer (the recommendation, findings, comparison table, plan or "
        "draft itself), never just a description of a file. Name any files you saved in one "
        "line at the end; they are extras to open.\n"
        "- In run_code, the task's files are in in/; save deliverables to out/ (they go to the "
        "user's library).\n"
        "</working>"
    )
    return "\n\n".join(parts)


DESIGN = """You are the architect inside Jarvis, a personal AI assistant. Jarvis has decided a \
request needs a team of sub-agents. Design the smallest team that will do it well and \
cheaply, as an AgentSystemSpec in JSON.

## Topologies (the "agents" list is read differently for each)
- single: 1 agent does it all. Prefer this when one agent with the right tools suffices.
- pipeline: 2+ agents in order, each builds on the previous output (e.g. researcher -> writer).
- supervisor_workers: agents[0] leads and delegates one task at a time to the others, \
reading each result, until it calls finish. For open-ended work needing judgement about what \
to do next.
- parallel_map_reduce: exactly 3 agents: [splitter, worker, combiner]. The splitter breaks the \
task into independent items; a copy of the worker handles each item in parallel; the combiner \
merges results. For "do X for each of N things".
- critic_loop: exactly 2 agents: [worker, critic]. The critic reviews and sends work back \
until it passes (max_rounds). For output where quality matters more than cost.

## Model tiers (cost rises about 5x per step)
- nano: classify, extract, short summaries.
- mini: the default: tool use, research, writing from sources, making files.
- pro: planning, leading a team, hard reasoning, reviewing. Use for leads and critics only \
when it matters.
- max: only for the lead, and only when the user asked for the best model.

## Rules
- Agent ids: short lowercase words (researcher, writer). role: one of ROLES; it picks the \
standing instructions the agent gets.
- instructions: specific to this task: what to produce, sources to prefer, the format, and \
when to stop. Don't restate generic rules; agents already get safety rules and their role.
- tools: give each agent only the tools it needs, chosen from AVAILABLE TOOLS. An agent that \
makes PDF, Word, PowerPoint, Excel or chart files needs run_code and the matching core skill.
- skills: grant matching skills from AVAILABLE SKILLS by id (e.g. core/pdf): file skills for \
agents that make files, and method skills (e.g. core/literature-review, core/fact-check) for \
agents whose task they cover.
- If the request asks for something to be emailed or a document deleted, give the agent that \
does it send_email or delete_document; the user approves each such step on an approval card \
in the app, so don't leave it out or plan to ask in chat.
- approval.required_for: add tools that should pause for the user beyond the ones that \
always do (send_email, delete_document always ask).
- deliverables: files the user asked for, e.g. ["report.docx"]. When they asked a question \
(what to buy, what the research says, how to do something), the answer goes in the chat: \
leave deliverables empty and don't have agents write the answer into a file.
- Keep it small: 1 to 4 agents usually; at most 8.
- If one of the user's SAVED TEMPLATES fits the request, reply with {"template_id": "<id>"} \
instead of a spec.

Reply with JSON only: {"spec": {"name": str, "goal": str, "topology": str, "agents": [{"id": \
str, "name": str, "role": str, "instructions": str, "tier": str, "tools": [str], "skills": \
[str], "max_steps": int|null}], "approval": {"required_for": [str]}, "max_rounds": int, \
"deliverables": [str]}} or {"template_id": str}."""


DEFAULT_ROLES = ("researcher", "writer", "analyst", "reviewer", "planner", "coder")


def design_request(
    request: str,
    deliverables: list[str],
    files: list[tuple[str, str]],
    tools: list[ToolInfo],
    skills: list[Skill],
    templates: list[tuple[str, str, str]],
    profile: str,
    best_model: bool,
    current: dict | None = None,
    roles: list[str] | None = None,
) -> str:
    parts = [f"REQUEST:\n{request}"]
    if deliverables:
        parts.append("DELIVERABLES WANTED: " + ", ".join(deliverables))
    if files:
        parts.append("USER FILES FOR THIS TASK:\n" + "\n".join(f"- {n} (id {i})" for i, n in files))
    if best_model:
        parts.append("The user asked for the best model: the lead may use tier max.")
    if profile:
        parts.append(f"ABOUT THE USER (for tone and format):\n{profile[:1500]}")
    parts.append(
        "AVAILABLE TOOLS:\n" + "\n".join(f"- {t.name} [{t.risk}]: {t.description}" for t in tools)
    )
    parts.append("AVAILABLE SKILLS:\n" + (catalogue(skills) or "(none)"))
    parts.append("ROLES: " + ", ".join(sorted({*(roles or DEFAULT_ROLES), "assistant"})))
    if templates:
        parts.append(
            "SAVED TEMPLATES:\n" + "\n".join(f"- {i}: {n}: {g[:300]}" for i, n, g in templates)
        )
    if current is not None:
        import json

        parts.append(
            "CURRENT SPEC (change it as the request asks, keep the rest):\n"
            + json.dumps(current, indent=1)[:12000]
        )
    return "\n\n".join(parts)
