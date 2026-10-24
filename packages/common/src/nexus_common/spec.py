"""AgentSystemSpec: the JSON Nexus writes to describe a team of sub-agents. The orchestrator
compiles it to a LangGraph graph; the web app draws the same nodes and edges.

Topologies, and how the agents list is read for each:
- single: one agent does the whole task.
- pipeline: agents run in order, each building on the previous one's output.
- supervisor_workers: the first agent leads and delegates to the others, one task at a time,
  until it finishes.
- parallel_map_reduce: exactly three agents. The first splits the task into items, the second
  handles each item (in parallel copies), the third combines their results.
- critic_loop: exactly two agents. The first does the work, the second reviews it and sends it
  back with feedback until it passes or max_rounds is reached.
- dev_project: Nexus's fixed software team (nexus_common.devteam) working in one workbench
  project: brief, plan, build, test and review, with fixes going back to the builders.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from nexus_common.devteam import DEV_IDS, MAX_ROUNDS, STAGES, dev_agents

Tier = Literal["nano", "mini", "pro", "max"]
Topology = Literal[
    "single", "pipeline", "supervisor_workers", "parallel_map_reduce", "critic_loop", "dev_project"
]
Lifetime = Literal["ephemeral", "persistent", "template"]

TOPOLOGIES: tuple[str, ...] = (
    "single",
    "pipeline",
    "supervisor_workers",
    "parallel_map_reduce",
    "critic_loop",
    "dev_project",
)
TIERS: tuple[str, ...] = ("nano", "mini", "pro", "max")
TIER_MODEL: dict[str, str] = {t: f"nexus-{t}" for t in TIERS}

# Rough list prices in USD per million tokens (input, output), only for the cost estimate shown
# before a run. LiteLLM records the real cost of every call.
TIER_PRICE: dict[str, tuple[float, float]] = {
    "nano": (0.05, 0.40),
    "mini": (0.25, 2.00),
    "pro": (1.25, 10.00),
    "max": (5.00, 40.00),
}
# Assumed per model call: prompt (instructions, history, tool results) and reply.
EST_INPUT_TOKENS = 6000
EST_OUTPUT_TOKENS = 700
# Fraction of an agent's max steps it is assumed to use. Dev agents get a generous 40 steps
# but use far fewer: real one-round builds cost $0.13 to $0.32 against an estimate of $0.81.
EST_STEP_SHARE = 0.5
EST_DEV_STEP_SHARE = 0.25

AGENT_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# A workbench project id (the workbench's own rule).
PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def tier_up(tier: str) -> str:
    i = TIERS.index(tier)
    return TIERS[min(i + 1, len(TIERS) - 1)]


def tier_down(tier: str) -> str:
    return TIERS[max(TIERS.index(tier) - 1, 0)]


class AgentSpec(BaseModel):
    id: str = Field(description="Short id: lowercase letters, digits and _, e.g. researcher")
    name: str = Field(max_length=60)
    # Used to pick role instructions, e.g. researcher, writer, analyst, reviewer, planner.
    role: str = Field(default="assistant", max_length=32)
    instructions: str = Field(max_length=6000)
    tier: Tier = "mini"
    tools: list[str] = Field(default_factory=list, max_length=12)
    skills: list[str] = Field(default_factory=list, max_length=12)
    max_steps: int | None = Field(default=None, ge=1, le=40)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        v = v.strip().lower()
        if not AGENT_ID.match(v):
            raise ValueError("agent id must be lowercase letters, digits and _, starting a letter")
        return v

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        return re.sub(r"[^a-z0-9_-]", "", v.strip().lower()) or "assistant"


class ApprovalSpec(BaseModel):
    # Tools that pause for the user before each call, besides the ones that always do.
    required_for: list[str] = Field(default_factory=list)


class AgentSystemSpec(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=4000)
    topology: Topology
    lifetime: Lifetime = "ephemeral"
    agents: list[AgentSpec] = Field(min_length=1, max_length=8)
    budget_usd: float | None = Field(default=None, gt=0, le=1000)
    approval: ApprovalSpec = Field(default_factory=ApprovalSpec)
    # Review rounds in a critic_loop, delegations per worker in supervisor_workers.
    max_rounds: int = Field(default=3, ge=1, le=10)
    # What the user should get back, e.g. ["report.docx", "data.xlsx"].
    deliverables: list[str] = Field(default_factory=list, max_length=10)
    # dev_project: the workbench project the team works in. An earlier project's id continues
    # that project; empty starts a new one (named after the system when it is saved).
    project: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _house_team(cls, data):
        """A dev_project always gets the house team, whatever agents were sent; a spec that
        already has it keeps its own copy (tiers or instructions changed on purpose)."""
        if isinstance(data, dict) and data.get("topology") == "dev_project":
            data = dict(data)
            agents = data.get("agents")
            ids = (
                [a.get("id") for a in agents if isinstance(a, dict)]
                if isinstance(agents, list)
                else []
            )
            if ids != list(DEV_IDS):
                data["agents"] = dev_agents()
            # Always the full review loop: a team that stops after one round to look cheaper
            # leaves the reviewer's fixes undone, so nothing is ever approved or deployed.
            data["max_rounds"] = MAX_ROUNDS
        return data

    @field_validator("project")
    @classmethod
    def _project(cls, v: str | None) -> str | None:
        v = (v or "").strip().lower()
        return v if PROJECT_ID.match(v) else None

    @model_validator(mode="after")
    def _shape(self):
        ids = [a.id for a in self.agents]
        if len(set(ids)) != len(ids):
            raise ValueError("agent ids must be unique")
        n = len(self.agents)
        need = {
            "single": n == 1,
            "pipeline": n >= 2,
            "supervisor_workers": n >= 2,
            "parallel_map_reduce": n == 3,
            "critic_loop": n == 2,
            "dev_project": ids == list(DEV_IDS),
        }
        if not need[self.topology]:
            rule = {
                "single": "exactly 1 agent",
                "pipeline": "at least 2 agents",
                "supervisor_workers": "a lead and at least 1 worker",
                "parallel_map_reduce": "exactly 3 agents: splitter, worker, combiner",
                "critic_loop": "exactly 2 agents: worker, critic",
                "dev_project": "the house team: " + ", ".join(DEV_IDS),
            }[self.topology]
            raise ValueError(f"{self.topology} needs {rule}, got {n}")
        return self

    @property
    def lead(self) -> AgentSpec:
        return self.agents[0]

    def agent(self, agent_id: str) -> AgentSpec:
        return next(a for a in self.agents if a.id == agent_id)

    def edges(self) -> list[dict]:
        """Directed edges for the UI, with what flows along them."""
        a = [x.id for x in self.agents]
        match self.topology:
            case "single":
                return []
            case "pipeline":
                return [
                    {"from": x, "to": y, "kind": "handoff"} for x, y in zip(a, a[1:], strict=False)
                ]
            case "supervisor_workers":
                return [{"from": a[0], "to": w, "kind": "delegate"} for w in a[1:]]
            case "parallel_map_reduce":
                return [
                    {"from": a[0], "to": a[1], "kind": "map"},
                    {"from": a[1], "to": a[2], "kind": "reduce"},
                ]
            case "critic_loop":
                return [
                    {"from": a[0], "to": a[1], "kind": "review"},
                    {"from": a[1], "to": a[0], "kind": "feedback"},
                ]
            case "dev_project":
                return [
                    {"from": "researcher", "to": "architect", "kind": "handoff"},
                    {"from": "architect", "to": "frontend", "kind": "handoff"},
                    {"from": "architect", "to": "backend", "kind": "handoff"},
                    {"from": "frontend", "to": "tester", "kind": "handoff"},
                    {"from": "backend", "to": "tester", "kind": "handoff"},
                    {"from": "tester", "to": "reviewer", "kind": "review"},
                    {"from": "reviewer", "to": "frontend", "kind": "feedback"},
                    {"from": "reviewer", "to": "backend", "kind": "feedback"},
                ]
        return []

    def nodes(self) -> list[dict]:
        out = [
            {"id": x.id, "name": x.name, "role": x.role, "tier": x.tier, "lead": i == 0}
            for i, x in enumerate(self.agents)
        ]
        if self.topology == "dev_project":
            for n in out:
                n["stage"] = STAGES[n["id"]]
        return out

    def estimate_usd(self, default_steps: int, map_items: int) -> float:
        """A rough upper-middle estimate of what a run costs, for the budget check."""
        total = 0.0
        share = EST_DEV_STEP_SHARE if self.topology == "dev_project" else EST_STEP_SHARE
        for i, x in enumerate(self.agents):
            steps = max(1.0, (x.max_steps or default_steps) * share)
            # How many times this agent runs: once, once per item, or once per round.
            copies = 1
            if self.topology == "parallel_map_reduce" and i == 1:
                copies = map_items
            elif self.topology == "critic_loop":
                copies = self.max_rounds
            elif self.topology == "dev_project" and i >= 2:
                # Build, test and review run once per review round.
                copies = self.max_rounds
            elif self.topology == "supervisor_workers":
                # The lead takes a turn after every delegation; each worker gets up to max_rounds.
                copies = self.max_rounds if i > 0 else 1 + self.max_rounds * (len(self.agents) - 1)
            p_in, p_out = TIER_PRICE[x.tier]
            per_call = (EST_INPUT_TOKENS * p_in + EST_OUTPUT_TOKENS * p_out) / 1_000_000
            total += per_call * steps * copies
        return round(total, 4)


def check_spec(spec: AgentSystemSpec, allowed_tools: set[str], skills: set[str]) -> list[str]:
    """Problems that make a valid-looking spec unusable for this user, in words Nexus can fix."""
    problems = []
    for a in spec.agents:
        bad = [t for t in a.tools if t not in allowed_tools]
        if bad:
            problems.append(
                f"agent {a.id}: tools not available to this user: {', '.join(bad)} "
                f"(available: {', '.join(sorted(allowed_tools)) or 'none'})"
            )
        missing = [s for s in a.skills if s not in skills]
        if missing:
            problems.append(f"agent {a.id}: unknown or disabled skills: {', '.join(missing)}")
    unknown = [t for t in spec.approval.required_for if t not in allowed_tools]
    if unknown:
        problems.append(f"approval.required_for names unknown tools: {', '.join(unknown)}")
    return problems
