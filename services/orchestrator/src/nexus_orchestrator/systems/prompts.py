"""Prompts for agent systems: each agent's system prompt, and the design prompt Nexus uses to
write an AgentSystemSpec."""

from datetime import UTC, datetime

from nexus_common.models import Skill
from nexus_common.skills import Layers, catalogue
from nexus_common.spec import AgentSpec, AgentSystemSpec
from nexus_common.toolinfo import ToolInfo, needs_approval


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
        case "flow":
            if agent.checks:
                return (
                    f"You check the work of {names[agent.checks]} against the goal and call "
                    "`verdict`: pass work that is good enough, or send it back with specific "
                    "feedback to fix."
                )
            before = (
                "You get the results of " + ", ".join(names[x] for x in agent.after) + ". "
                if agent.after
                else "You start straight away. "
            )
            checker = next((a for a in spec.agents if a.checks == agent.id), None)
            if checker is not None:
                after = (
                    f"{names[checker.id]} checks your work and may send it back with feedback; "
                    "then revise and reply with the full revised result."
                )
            elif agent.id == spec.flow_end:
                after = "Your result is the final answer the user sees."
            else:
                after = "Your result goes to the next agents in the flow."
            return f"You are one step in a flow of agents. {before}{after}"
        case "dev_project":
            return (
                "You are in a software team working in one project folder that every agent "
                "shares: the researcher writes BRIEF.md, the architect PLAN.md, the frontend and "
                "backend builders build it side by side, the tester tests it and the reviewer "
                "approves it or sends fixes back to the builders. "
                + {
                    "researcher": "You write the brief.",
                    "architect": "You write the plan; the user sees it in the chat.",
                    "frontend": "You build the front end.",
                    "backend": "You build the back end.",
                    "tester": "You test what the builders made.",
                    "reviewer": "You review the work; your verdict decides whether it ships.",
                }.get(agent.id, "")
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
        "draft itself), never just a description of a file. If you saved files, name them in "
        "one line at the end; they are extras to open. If you saved none, say nothing about "
        "files.\n"
        "- In run_code, the task's files are in in/; save deliverables to out/ (they go to the "
        "user's library).\n"
        "</working>"
    )
    return "\n\n".join(parts)


DESIGN = """You are the architect inside a personal AI assistant. The assistant has decided a \
request needs a team of sub-agents. Design the flow this task needs, then the smallest team \
that runs it well and cheaply, as an AgentSystemSpec in JSON.

## First, think about the flow (in "plan", before the spec)
Work out how a careful expert would do this particular task, step by step:
- Understand: does it depend on things outside the request (what something looks like, \
current facts, prices, specs, the user's documents, how others solved it)? Then research comes \
first, and what is made later is built on that research, not on guesses.
- Plan: does it need a design, outline or structure decided before making it?
- Make: what gets produced (an answer, a draft, a file, a model, a dataset)?
- Check: what would go wrong unchecked (likeness, accuracy, sources, it doesn't print, it \
doesn't add up)? Then a checker reviews the work and sends it back until it passes.
- Side by side: which parts don't depend on each other and can run at the same time?
Skip the steps the task doesn't need: a short lookup is one agent; not every task needs \
research or a checker. Match the flow to the task, not the task to a habit.

## Topologies (the "agents" list is read differently for each)
- single: 1 agent does it all. For tasks one agent with the right tools does well.
- flow: a flow you design: research, plan, make and check steps wired the way this task \
needs. Each agent lists in "after" the agents whose results it needs; agents with nothing in \
common run side by side. A checker sets "checks" to the agent whose work it reviews: it gets \
that work, calls verdict, and sends it back until it passes (up to max_rounds); later agents \
put the checker in their "after" so they get the checked work. Exactly one agent comes last \
(nothing after it): its result, or the work it passes if it is a checker, is the answer. The \
usual choice for anything with more than one kind of step.
- pipeline: 2+ agents strictly in order, each building on the previous output. A flow with \
no branches or checks; prefer flow when there is any.
- supervisor_workers: agents[0] leads and delegates one task at a time to the others, \
reading each result, until it calls finish. For open-ended work where the next step depends on \
what the last one found.
- parallel_map_reduce: exactly 3 agents: [splitter, worker, combiner]. The splitter breaks the \
task into independent items; a copy of the worker handles each item in parallel; the combiner \
merges results. For "do X for each of N things", when N isn't known up front.
- critic_loop: exactly 2 agents: [worker, critic]. Same as a flow of one maker and its checker.
- dev_project: Nexus's software team, ONLY for a program the user will run or use: an app, \
website, game, tool, script or bot to build, fix or extend. Its agents are fixed: send \
"agents": [] and only give name (the app's short name, e.g. "2048"), goal and project. \
project: to change or extend one of the user's EXISTING PROJECTS (a follow-up such as "add a \
high score table"), its id; for a new app, null. A file made with code (a 3D model, chart, \
document, dataset or image) is not software: give that to a flow or single agent with \
run_code and the matching skill.

## Examples of flows (ids and tools are illustrations)
- "Make a 3D model of a Minion to print": flow: reference (web_search, web_fetch: the \
character's shape, proportions, parts and colours, as measurements to model from) -> modeller \
(run_code, core/3d-modeling: builds it from the reference, renders previews, checks it \
prints) -> checker (checks modeller: likeness to the reference and printability, from the \
check report and previews; sends back with fixes).
- "Compare the best three e-bikes under 2 lakh": flow: researcher_a and researcher_b side by \
side (different sources or halves of the field) -> analyst (after both: the comparison table \
and the pick).
- "Write a blog post on RAG evaluation": flow: researcher -> writer -> editor (checks \
writer: accuracy against the sources, clarity).
- "What's the capital of Peru and its population?": single.
- "Summarise each of these 12 papers": parallel_map_reduce.

## Model tiers (cost rises about 5x per step)
- nano: classify, extract, short summaries.
- mini: the default: tool use, research, writing from sources, making files.
- pro: planning, leading a team, hard reasoning, reviewing. Use for leads and checkers only \
when it matters.
- max: only for the lead, and only when the user asked for the best model.

## Rules
- Agent ids: short lowercase words (researcher, writer). role: one of ROLES; it picks the \
standing instructions the agent gets.
- instructions: specific to this task: what to produce, sources to prefer, the format, what \
it gets from earlier agents and hands on, and when to stop. Don't restate generic rules; \
agents already get safety rules and their role.
- tools: give each agent only the tools it needs, chosen from AVAILABLE TOOLS. run_code is for \
computing and making files, not a default: a researcher or writer doesn't need it. An agent \
that makes PDF, Word, PowerPoint, Excel, chart or 3D files needs run_code and the matching core \
skill.
- skills: grant matching skills from AVAILABLE SKILLS by id (e.g. core/pdf): file skills for \
agents that make files, and method skills (e.g. core/literature-review, core/fact-check) for \
agents whose task they cover. When the task is about Nexus itself (how it works, comparing a \
tool or framework with it, changing it), give every agent core/nexus-system so it works from \
the facts instead of guessing.
- If the request asks for something to be emailed or a document deleted, give the agent that \
does it send_email or delete_document; the user approves each such step on an approval card \
in the app, so don't leave it out or plan to ask in chat.
- If the user asks to move a software project to their own GitHub, a single agent with \
github_transfer does it (it asks the user first); the request must name their GitHub \
username.
- approval.required_for: add tools that should pause for the user beyond the ones that \
always do (send_email, delete_document always ask).
- deliverables: files the user asked for, e.g. ["report.docx"]. When they asked a question \
(what to buy, what the research says, how to do something), the answer goes in the chat: \
leave deliverables empty and don't have agents write the answer into a file.
- Keep it small: 1 to 4 agents usually; at most 8.
- Software to build, fix or extend always gets dev_project, never another topology.
- If one of the user's SAVED TEMPLATES fits the request, reply with {"template_id": "<id>"} \
instead of a spec.
- If one of PAST RESULTS already is what the request asks for (the same thing again, with no \
change asked for), reply with {"existing": "<run id>"} instead of a spec: the assistant opens \
it and asks what to change, which saves a rebuild. Not when the request asks for a new, \
another or different one, or for changes: then design a spec (a change to a past result \
starts from its files).

Reply with JSON only: {"plan": str (the flow in a few lines: steps, what each needs, what \
runs side by side, what is checked), "spec": {"name": str, "goal": str, "topology": str, \
"agents": [{"id": str, "name": str, "role": str, "instructions": str, "tier": str, "tools": \
[str], "skills": [str], "max_steps": int|null, "after": [str], "checks": str|null}], \
"approval": {"required_for": [str]}, "max_rounds": int, "deliverables": [str], "project": \
str|null}} or {"template_id": str} or {"existing": str}."""


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
    projects: list[tuple[str, str, str]] | None = None,
    software: bool = False,
    past: list[tuple[str, str, str, list[str]]] | None = None,
    based_on: str = "",
) -> str:
    parts = [f"REQUEST:\n{request}"]
    if software:
        parts.append("This request is software to build, fix or extend: use dev_project.")
    if projects:
        parts.append(
            "EXISTING PROJECTS (id: name: goal), newest first:\n"
            + "\n".join(f"- {i}: {n}: {g[:200]}" for i, n, g in projects)
        )
    if based_on:
        parts.append(
            f"CHANGE TO A PAST RESULT: {based_on}\nIts files are among the user's files for this "
            "task. The team starts from them and changes what the request asks, keeping the rest; "
            "it doesn't redo the work from scratch."
        )
    elif past:
        parts.append(
            "PAST RESULTS (run id: name: goal: files), newest first:\n"
            + "\n".join(
                f"- {i}: {n}: {g[:200]}: {', '.join(f[:8]) or 'no files'}" for i, n, g, f in past
            )
        )
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
