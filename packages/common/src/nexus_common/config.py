"""Runtime configuration the owner changes on the Admin page's Configuration tab, without a
redeploy.

`.env` (through compose.yaml) still gives every setting its value at start-up; that is the
default. The owner's changes are stored as overrides in the `app_settings` row "config", and
every API replica and worker lays them over its own `Settings` object:

- the replica that saves a change applies it at once and bumps a version in Valkey;
- every other process checks that version every few seconds (and re-reads the row every
  minute anyway, in case Valkey lost it) and applies what changed.

Code reads settings when it needs them (`deps.settings.chat_model`), so most changes take effect
on the next request, turn or job. A few are read once when a service starts (a job timeout arq
registers, the sign-in scopes); those name the services in `restart`. A process applies them
only when it starts, and reports which ones are waiting for a restart, so the Admin page can say
so.

Only plain settings are listed. Secrets (API keys, passwords, tokens) are never here: they stay
encrypted in secrets/nexus.sops.env and change with `./scripts/secrets.sh set NAME`. Neither
are addresses and wiring (database, Valkey and service URLs, domains, ports, replicas), which
only make sense with a redeploy.
"""

import asyncio
import json
import logging
import re
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus_common.models import AppSetting
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

CONFIG_KEY = "config"
VERSION_KEY = "nexus:config:version"
APPLIED_PREFIX = "nexus:config:applied:"
# How often a process looks for a new version, re-reads the row regardless, and how long its
# "applied" report lives without a refresh.
CHECK_EVERY_S = 5.0
REREAD_EVERY_S = 60.0
REPORT_TTL_S = 30

Kind = Literal["bool", "int", "float", "text", "choice", "list"]
MODEL_TIERS = ("nexus-nano", "nexus-mini", "nexus-pro", "nexus-max")
GOOGLE_SERVICES = ("gmail", "calendar", "contacts", "tasks", "drive")
ROLES = ("owner", "admin", "member", "viewer")
# Words no listed setting may contain: a guard against ever listing a secret by mistake.
SECRET_WORDS = ("key", "secret", "password", "token", "dsn")


@dataclass(frozen=True)
class Option:
    name: str  # the Settings attribute; the .env name is the same in capitals
    group: str
    label: str
    help: str
    kind: Kind
    choices: tuple[str, ...] = ()
    min: float | None = None
    max: float | None = None
    unit: str = ""
    # Services that read it only at start-up: there it takes effect after a restart.
    restart: tuple[str, ...] = ()
    # For text and list settings: an empty value is allowed (and means what `help` says).
    allow_empty: bool = True

    @property
    def env(self) -> str:
        return self.name.upper()


GROUPS = (
    "Logging",
    "Models",
    "Budgets",
    "Agent teams",
    "Memory and context",
    "Sign-in and access",
    "Rate limits",
    "Alerts and backups",
    "Self-review",
    "Email",
    "Apps and links",
)

OPTIONS: tuple[Option, ...] = (
    # Logging
    Option(
        "log_format",
        "Logging",
        "Log format",
        "text is one readable line per event, for compose.sh logs. json is one JSON object per "
        "line with the request, user, job and trace ids as keys, for a log collector such as "
        "Loki, Datadog or CloudWatch.",
        "choice",
        choices=("text", "json"),
    ),
    Option(
        "log_level",
        "Logging",
        "Log level",
        "The least serious lines written. INFO is normal; DEBUG is very chatty and is for chasing "
        "a problem for a few minutes; WARNING keeps only problems.",
        "choice",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    ),
    # Models
    Option(
        "chat_model",
        "Models",
        "Chat model",
        "The model tier the assistant answers with in chat. Tiers are LiteLLM aliases "
        "(infra/litellm/config.yaml): nano is cheapest, max is the most capable and costly.",
        "choice",
        choices=MODEL_TIERS,
    ),
    Option(
        "route_model",
        "Models",
        "Router model",
        "Before the assistant answers, this model decides whether a message is work for a team. "
        "Off lets the assistant decide on its own.",
        "choice",
        choices=("", *MODEL_TIERS),
    ),
    Option(
        "route_min_chars",
        "Models",
        "Router skips messages shorter than",
        'Short messages such as "hi" or "thanks" go straight to the assistant without asking '
        "the router.",
        "int",
        min=0,
        max=2000,
        unit="characters",
    ),
    Option(
        "design_model",
        "Models",
        "Team designer model",
        "Designs agent teams (who does what, with which tools and tier) and drafts skills "
        "learned from long runs.",
        "choice",
        choices=MODEL_TIERS,
    ),
    Option(
        "memory_model",
        "Models",
        "Memory model",
        "Extracts memories after each turn, summarises quiet chats and rebuilds profiles. Runs "
        "often, so the cheapest tier is usually right.",
        "choice",
        choices=MODEL_TIERS,
    ),
    Option(
        "max_tier_roles",
        "Models",
        "Roles allowed the max tier",
        "Roles whose team leads may use nexus-max when the best model is asked for. Others get "
        "pro at most.",
        "list",
        choices=ROLES,
    ),
    # Budgets
    Option(
        "default_budget_usd",
        "Budgets",
        "Member budget",
        "Model spend allowed per person per budget period, set when their gateway key is "
        "created on first chat. Existing people keep theirs; change those on the People page.",
        "float",
        min=0,
        max=1000,
        unit="USD",
    ),
    Option(
        "owner_budget_usd",
        "Budgets",
        "Owner budget",
        "The same, for owners.",
        "float",
        min=0,
        max=10000,
        unit="USD",
    ),
    Option(
        "litellm_budget_duration",
        "Budgets",
        "Budget period",
        "How often budgets reset, as LiteLLM writes it: 30d, 7d, 24h. Applies to keys created "
        "from now on and to the spend shown against each budget.",
        "text",
        allow_empty=False,
    ),
    Option(
        "default_run_budget_usd",
        "Budgets",
        "Team run budget",
        "What one agent-team run may spend unless the person asks for more.",
        "float",
        min=0.01,
        max=100,
        unit="USD",
    ),
    Option(
        "dev_run_budget_usd",
        "Budgets",
        "Software team run budget",
        "The default for a software team (plan, build, test, review), which works in rounds.",
        "float",
        min=0.01,
        max=100,
        unit="USD",
    ),
    Option(
        "max_run_budget_usd",
        "Budgets",
        "Most one run may spend",
        "The cap on any single run, whatever was asked. Each run is also limited by what its "
        "owner has left this period.",
        "float",
        min=0.01,
        max=500,
        unit="USD",
    ),
    # Agent teams
    Option(
        "agent_max_steps",
        "Agent teams",
        "Model calls per agent turn",
        "How many model calls one agent may make in one turn of a run before it must stop and "
        "hand over what it has. Software team agents have their own, higher limit.",
        "int",
        min=1,
        max=100,
    ),
    Option(
        "max_parallel_agents",
        "Agent teams",
        "Agents working at once",
        "In a parallel (map-reduce) team, how many workers run side by side.",
        "int",
        min=1,
        max=16,
    ),
    Option(
        "max_map_items",
        "Agent teams",
        "Items a parallel team may fan out to",
        "The most pieces a parallel team splits a job into.",
        "int",
        min=1,
        max=50,
    ),
    Option(
        "learn_after_tool_calls",
        "Agent teams",
        "Offer to learn a skill after",
        "After a successful run with at least this many tool calls, the assistant drafts a skill "
        "from it for review. 0 turns this off.",
        "int",
        min=0,
        max=500,
        unit="tool calls",
    ),
    Option(
        "sandbox_timeout_s",
        "Agent teams",
        "Code sandbox time limit",
        "How long one run_code call may take.",
        "int",
        min=10,
        max=1800,
        unit="seconds",
        restart=("orchestrator",),
    ),
    Option(
        "workbench_timeout_s",
        "Agent teams",
        "Workbench command time limit",
        "How long one command in a software team's workbench may run unless the agent asks for "
        "longer. The workbench's own ceiling (WORKBENCH_MAX_TIMEOUT_S in .env) still applies.",
        "int",
        min=10,
        max=3600,
        unit="seconds",
    ),
    Option(
        "run_job_timeout_s",
        "Agent teams",
        "Longest a run may take",
        "Wall-clock limit for one agent-team run, including a software team's build and deploy.",
        "int",
        min=300,
        max=14400,
        unit="seconds",
        restart=("orchestrator",),
    ),
    # Memory and context
    Option(
        "chat_history_limit",
        "Memory and context",
        "Messages sent with each turn",
        "How much of the chat goes to the model each turn. Older messages are covered by the "
        "chat's summary.",
        "int",
        min=4,
        max=200,
        unit="messages",
    ),
    Option(
        "memory_top_k",
        "Memory and context",
        "Memories per turn",
        "The memories closest to the message that are put in the prompt.",
        "int",
        min=0,
        max=50,
    ),
    Option(
        "episode_top_k",
        "Memory and context",
        "Past-chat summaries per turn",
        "Summaries of earlier chats close to the message that are put in the prompt. 0 turns "
        "this off.",
        "int",
        min=0,
        max=10,
    ),
    Option(
        "chunk_top_k",
        "Memory and context",
        "Document excerpts per turn",
        "Best-matching excerpts from attached documents put in the prompt.",
        "int",
        min=0,
        max=50,
    ),
    Option(
        "inline_document_chars",
        "Memory and context",
        "Send documents whole below",
        "Attached documents shorter than this in total go into the prompt whole instead of as "
        "excerpts.",
        "int",
        min=0,
        max=200000,
        unit="characters",
    ),
    Option(
        "episode_idle_minutes",
        "Memory and context",
        "Summarise a chat after",
        "A chat is summarised for memory once it has been quiet this long.",
        "int",
        min=1,
        max=1440,
        unit="minutes",
    ),
    Option(
        "graph_enabled",
        "Memory and context",
        "Build the knowledge graph",
        "Note the people, projects, places, topics and tools memories, past chats and files are "
        "about, and how they relate, in the same memory-model call that saves memories (files "
        "and memories you add yourself take one small call per batch). Shown under Connections "
        "on the Memory page. Off stops adding to it; what is there stays.",
        "bool",
    ),
    Option(
        "memory_nightly_hour_utc",
        "Memory and context",
        "Nightly memory upkeep hour (UTC)",
        "When memories decay and merge and profiles are rebuilt. 21 UTC is 02:30 or 03:00 IST "
        "with the minute below.",
        "int",
        min=0,
        max=23,
        restart=("orchestrator",),
    ),
    Option(
        "memory_nightly_minute_utc",
        "Memory and context",
        "Nightly memory upkeep minute",
        "The minute past that hour.",
        "int",
        min=0,
        max=59,
        restart=("orchestrator",),
    ),
    Option(
        "max_upload_mb",
        "Memory and context",
        "Largest upload",
        "The biggest file anyone can upload in a chat or the Library.",
        "int",
        min=1,
        max=500,
        unit="MB",
    ),
    # Sign-in and access
    Option(
        "signup_policy",
        "Sign-in and access",
        "Who can join",
        "invite_only: after the owner, only invited addresses (and approved access requests) "
        "can sign in. open: anyone with a Google account joins as a member, so only use it "
        "briefly.",
        "choice",
        choices=("invite_only", "open"),
    ),
    Option(
        "access_requests_enabled",
        "Sign-in and access",
        "Allow access requests",
        "With invite only, someone refused at sign-in may ask for an invite from the login "
        "page. Requests appear on the People page.",
        "bool",
    ),
    Option(
        "access_request_cooldown_days",
        "Sign-in and access",
        "Wait after a denied request",
        "How long before the same address may ask again.",
        "int",
        min=0,
        max=365,
        unit="days",
    ),
    Option(
        "access_request_max_pending",
        "Sign-in and access",
        "Open requests kept at once",
        "Beyond this, new requests are refused until some are answered.",
        "int",
        min=1,
        max=10000,
    ),
    Option(
        "access_request_notify",
        "Sign-in and access",
        "Email new requests to",
        "Comma-separated addresses. Empty emails every active owner and admin. Needs email set "
        "up below.",
        "text",
    ),
    Option(
        "session_ttl_days",
        "Sign-in and access",
        "Stay signed in for",
        "How long a sign-in lasts on a device. Applies to sign-ins from now on.",
        "int",
        min=1,
        max=365,
        unit="days",
    ),
    Option(
        "google_services",
        "Sign-in and access",
        "Google services asked for at sign-in",
        "Each one must also be enabled in Google Cloud with its scope on the consent screen "
        "(README: Google services). People can untick any of them. Empty asks for name and "
        "email only.",
        "list",
        choices=GOOGLE_SERVICES,
        restart=("api",),
    ),
    # Rate limits
    Option(
        "rate_limit_enabled",
        "Rate limits",
        "Rate limits on",
        "Turns every limit below on or off. Model spend stays capped by budgets either way.",
        "bool",
    ),
    Option(
        "rate_limit_api_per_minute",
        "Rate limits",
        "API requests per IP",
        "Per minute. 0 turns this limit off.",
        "int",
        min=0,
        max=100000,
        unit="per minute",
    ),
    Option(
        "rate_limit_auth_per_minute",
        "Rate limits",
        "Sign-in attempts per IP",
        "Per minute, access requests included. 0 turns this limit off.",
        "int",
        min=0,
        max=10000,
        unit="per minute",
    ),
    Option(
        "rate_limit_messages_per_minute",
        "Rate limits",
        "Chat messages per person",
        "Per minute. 0 turns this limit off.",
        "int",
        min=0,
        max=10000,
        unit="per minute",
    ),
    Option(
        "rate_limit_uploads_per_minute",
        "Rate limits",
        "Uploads per person",
        "Per minute. 0 turns this limit off.",
        "int",
        min=0,
        max=10000,
        unit="per minute",
    ),
    Option(
        "rate_limit_runs_per_hour",
        "Rate limits",
        "Team runs started per person",
        "Per hour, from the Agents page. 0 turns this limit off.",
        "int",
        min=0,
        max=10000,
        unit="per hour",
    ),
    # Alerts and backups
    Option(
        "alert_interval_s",
        "Alerts and backups",
        "Check for problems every",
        "How often one API replica runs the alert checks. 0 turns alerts off.",
        "int",
        min=0,
        max=3600,
        unit="seconds",
        restart=("api",),
    ),
    Option(
        "alert_api_errors_5m",
        "Alerts and backups",
        "Alert on API errors",
        "Raise an alert when this many requests fail with a server error within 5 minutes. 0 "
        "turns this check off.",
        "int",
        min=0,
        max=10000,
        unit="in 5 minutes",
    ),
    Option(
        "alert_job_failures_15m",
        "Alerts and backups",
        "Alert on failed jobs",
        "Raise an alert when this many background jobs fail within 15 minutes. 0 turns this "
        "check off.",
        "int",
        min=0,
        max=10000,
        unit="in 15 minutes",
    ),
    Option(
        "alert_llm_failures_15m",
        "Alerts and backups",
        "Alert on failed model calls",
        "Raise an alert when this many model calls fail within 15 minutes (from LiteLLM's spend "
        "logs: provider errors, timeouts, rate limits). 0 turns this check off.",
        "int",
        min=0,
        max=10000,
        unit="in 15 minutes",
    ),
    Option(
        "alert_daily_spend_usd",
        "Alerts and backups",
        "Alert on daily model spend",
        "Raise an alert when model calls since midnight UTC cost more than this, across "
        "everyone. 0 turns this check off.",
        "float",
        min=0,
        max=10000,
        unit="dollars",
    ),
    Option(
        "alert_budget_percent",
        "Alerts and backups",
        "Alert when a budget is nearly spent",
        "Raise an alert when someone has spent this share of their budget for the period. 0 "
        "turns this check off.",
        "int",
        min=0,
        max=100,
        unit="%",
    ),
    Option(
        "alert_email",
        "Alerts and backups",
        "Email alerts to owners",
        "When an alert is raised and when it clears. Needs email set up below; off keeps alerts "
        "on the Admin page only.",
        "bool",
    ),
    Option(
        "backup_stale_hours",
        "Alerts and backups",
        "Warn when the last backup is older than",
        "Shown on the Status tab and raised as an alert.",
        "int",
        min=1,
        max=720,
        unit="hours",
    ),
    # Self-review
    Option(
        "improve_every_days",
        "Self-review",
        "Review how it is used every",
        "How often the assistant looks over recent use (counts, failures and anonymous topics, "
        "never chats or names) and drafts improvements for you to approve. 0 stops the "
        "schedule; Review now on the Improvements tab still works.",
        "int",
        min=0,
        max=90,
        unit="days",
    ),
    Option(
        "improve_model",
        "Self-review",
        "Proposal model",
        "Drafts the proposals from what the review found. One call per review, so mini costs "
        "a cent or two.",
        "choice",
        choices=MODEL_TIERS,
    ),
    Option(
        "improve_topic_model",
        "Self-review",
        "Topic model",
        'Turns each request into a short anonymous topic, such as "trip planning", in batches. '
        "Runs over every request in the window, so the cheapest tier is right.",
        "choice",
        choices=MODEL_TIERS,
    ),
    Option(
        "improve_read_requests",
        "Self-review",
        "Learn from request topics",
        "Off, the review uses only counts: tools, failures, teams and skills. On, a model also "
        "reads each request once to name its topic; only the topic counts are kept.",
        "bool",
    ),
    Option(
        "improve_min_count",
        "Self-review",
        "A topic counts from",
        "Topics asked fewer times than this are left out, so one person's one-off request "
        "never shapes a proposal.",
        "int",
        min=1,
        max=100,
        unit="requests",
    ),
    Option(
        "improve_max_proposals",
        "Self-review",
        "Proposals per review",
        "At most this many new proposals each time.",
        "int",
        min=1,
        max=20,
    ),
    Option(
        "improve_repo",
        "Self-review",
        "Repository for code proposals",
        "owner/name of the Nexus repository. An approved code or tool proposal becomes a pull "
        "request there, opened by the Nexus GitHub App (installed on it with contents and pull "
        "requests write). Empty keeps approved code proposals as a spec only.",
        "text",
    ),
    Option(
        "improve_base_branch",
        "Self-review",
        "Base branch",
        "The branch those pull requests are opened against.",
        "text",
        allow_empty=False,
    ),
    # Email
    Option(
        "smtp_host",
        "Email",
        "SMTP server",
        "For agents' emails (always approved first), access-request notices and alerts. Empty "
        "turns email off. Gmail: smtp.gmail.com with an app password. The password is a "
        "secret: ./scripts/secrets.sh set SMTP_PASSWORD.",
        "text",
    ),
    Option("smtp_port", "Email", "SMTP port", "587 for STARTTLS.", "int", min=1, max=65535),
    Option(
        "smtp_username",
        "Email",
        "SMTP username",
        "Usually the sending address. Empty sends without signing in.",
        "text",
    ),
    Option(
        "smtp_from",
        "Email",
        "From address",
        "Shown as the sender. Empty uses the username.",
        "text",
    ),
    Option(
        "smtp_starttls",
        "Email",
        "Use STARTTLS",
        "Encrypt the connection to the server. Leave on unless the server says otherwise.",
        "bool",
    ),
    # Apps and links
    Option(
        "web_search_provider",
        "Apps and links",
        "Web search provider",
        "auto uses Tavily or Brave when its key is set, otherwise DuckDuckGo (free, no key, less "
        "reliable). Keys are secrets: ./scripts/secrets.sh set TAVILY_API_KEY.",
        "choice",
        choices=("auto", "tavily", "brave", "duckduckgo"),
    ),
    Option(
        "member_app_limit",
        "Apps and links",
        "Apps a member may run at once",
        "The owner has no limit.",
        "int",
        min=0,
        max=100,
    ),
    Option(
        "app_cookie_hours",
        "Apps and links",
        "Private app sign-in lasts",
        "How long one sign-in to a private app lasts on that app's address.",
        "int",
        min=1,
        max=720,
        unit="hours",
    ),
    Option(
        "reserved_hostnames",
        "Apps and links",
        "Names apps may not take",
        "Comma-separated, on top of nexus, www, mail, api, admin and traefik: for example the "
        "names your other servers use under the same domain.",
        "text",
    ),
    Option(
        "deploy_timeout_s",
        "Apps and links",
        "Deploy time limit",
        "How long building, starting, health-checking and screenshotting an app may take.",
        "int",
        min=60,
        max=7200,
        unit="seconds",
        restart=("api", "orchestrator"),
    ),
    Option(
        "langfuse_url",
        "Apps and links",
        "Langfuse address",
        "Where the Admin page links to for per-call traces and cost. Must match LANGFUSE_PORT.",
        "text",
        allow_empty=False,
    ),
)

BY_NAME = {o.name: o for o in OPTIONS}

for _o in OPTIONS:
    assert not any(w in _o.name for w in SECRET_WORDS), f"{_o.name} looks like a secret"
    assert _o.group in GROUPS, _o.name
    assert _o.name in Settings.model_fields, _o.name

_DURATION = re.compile(r"^[1-9]\d{0,3}[smhd]$")
_EMAIL = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")
_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_REPO = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
TEXT_MAX = 500


class ConfigError(ValueError):
    """A value the owner typed that can't be used; the message says why, for the form."""


def _text_checks(opt: Option, value: str) -> str:
    if opt.name == "litellm_budget_duration" and not _DURATION.match(value):
        raise ConfigError("Use a number and a unit, such as 30d, 7d or 24h.")
    if opt.name == "access_request_notify":
        bad = [e for e in (p.strip() for p in value.split(",")) if e and not _EMAIL.match(e)]
        if bad:
            raise ConfigError(f"Not an email address: {bad[0]}")
        return ", ".join(p.strip() for p in value.split(",") if p.strip())
    if opt.name == "reserved_hostnames":
        names = [p.strip().lower() for p in value.split(",") if p.strip()]
        bad = [n for n in names if not _HOSTNAME.match(n)]
        if bad:
            raise ConfigError(f"Not a single host name: {bad[0]}")
        return ",".join(names)
    if opt.name == "improve_repo" and value and not _REPO.match(value):
        raise ConfigError("Use owner/name, such as SS12dev/nexus-system.")
    if opt.name == "improve_base_branch" and not _BRANCH.match(value):
        raise ConfigError("Not a branch name.")
    if opt.name == "smtp_host" and value and not _HOST.match(value):
        raise ConfigError("Use a host name such as smtp.gmail.com.")
    if opt.name in ("smtp_from", "smtp_username") and value and len(value) > 254:
        raise ConfigError("Too long.")
    if opt.name == "langfuse_url" and not re.match(r"^https?://\S+$", value):
        raise ConfigError("Use a full address starting with http:// or https://.")
    return value


def validate(opt: Option, raw: Any) -> Any:
    """The value to store and apply, or ConfigError."""
    if opt.kind == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.lower() in ("true", "false"):
            return raw.lower() == "true"
        raise ConfigError("Choose on or off.")
    if opt.kind in ("int", "float"):
        if isinstance(raw, bool) or raw is None or raw == "":
            raise ConfigError("Enter a number.")
        try:
            num = float(raw)
        except (TypeError, ValueError):
            raise ConfigError("Enter a number.") from None
        if opt.kind == "int":
            if num != int(num):
                raise ConfigError("Enter a whole number.")
            num = int(num)
        if opt.min is not None and num < opt.min:
            raise ConfigError(f"The smallest allowed is {_fmt(opt.min)}.")
        if opt.max is not None and num > opt.max:
            raise ConfigError(f"The largest allowed is {_fmt(opt.max)}.")
        return num
    if not isinstance(raw, str):
        raise ConfigError("Enter text.")
    value = raw.strip()
    if len(value) > TEXT_MAX:
        raise ConfigError(f"Keep it under {TEXT_MAX} characters.")
    if opt.kind == "choice":
        if value not in opt.choices:
            raise ConfigError("Pick one of the listed values.")
        return value
    if opt.kind == "list":
        items = [p.strip().lower() for p in value.split(",") if p.strip()]
        bad = [i for i in items if i not in opt.choices]
        if bad:
            raise ConfigError(f"Unknown: {bad[0]}. Allowed: {', '.join(opt.choices)}.")
        if not items and not opt.allow_empty:
            raise ConfigError("Pick at least one.")
        # Keep the listed order and drop repeats, so equal choices compare equal.
        return ",".join(c for c in opt.choices if c in items)
    if not value and not opt.allow_empty:
        raise ConfigError("This can't be empty.")
    return _text_checks(opt, value)


def _fmt(n: float) -> str:
    return str(int(n)) if n == int(n) else str(n)


# Per Settings object: its values from the environment (the defaults), the stored version it
# last applied, and the restart-only settings whose new values are waiting for a restart.
@dataclass
class _State:
    defaults: dict[str, Any]
    version: int = 0
    pending_restart: tuple[str, ...] = ()
    applied_at: datetime | None = None


_states: dict[int, _State] = {}


def _state(settings: Settings) -> _State:
    st = _states.get(id(settings))
    if st is None:
        st = _State(defaults={o.name: getattr(settings, o.name) for o in OPTIONS})
        _states[id(settings)] = st
    return st


def defaults(settings: Settings) -> dict[str, Any]:
    """What each setting is without overrides: the values the process started with from .env."""
    return dict(_state(settings).defaults)


def forget(settings: Settings) -> None:
    """Drop what is remembered about `settings` (tests reuse one object)."""
    _states.pop(id(settings), None)


def apply(
    settings: Settings,
    overrides: dict[str, Any],
    service: str,
    *,
    version: int = 0,
    starting: bool = False,
) -> list[str]:
    """Lay `overrides` over the defaults on `settings`, in place. Settings that `service` reads
    only at start-up change only when `starting`; otherwise they are recorded as waiting for a
    restart. Returns the names that changed."""
    st = _state(settings)
    changed, pending = [], []
    for opt in OPTIONS:
        want = st.defaults[opt.name]
        if opt.name in overrides:
            try:
                want = validate(opt, overrides[opt.name])
            except ConfigError as exc:
                log.warning("config.ignored", extra={"setting": opt.name, "error": str(exc)})
        if getattr(settings, opt.name) == want:
            continue
        if service in opt.restart and not starting:
            pending.append(opt.name)
            continue
        setattr(settings, opt.name, want)
        changed.append(opt.name)
    st.version, st.pending_restart, st.applied_at = version, tuple(pending), datetime.now(UTC)
    if changed:
        log.info("config.applied", extra={"version": version, "changed": ",".join(changed)})
    if {"log_format", "log_level"} & set(changed):
        from nexus_common.logs import configure_logging

        configure_logging(settings, service)
    return changed


async def load(db: AsyncSession) -> tuple[dict[str, Any], int, AppSetting | None]:
    """The stored overrides, their version and the row (for who changed it and when)."""
    row = await db.get(AppSetting, CONFIG_KEY)
    if row is None:
        return {}, 0, None
    value = row.value or {}
    values = {k: v for k, v in (value.get("values") or {}).items() if k in BY_NAME}
    return values, int(value.get("version") or 0), row


async def save(
    db: AsyncSession, changes: dict[str, Any], actor_id: Any
) -> tuple[dict[str, Any], int, dict[str, Any]]:
    """Validate and store `changes` (a value, or None to go back to the default). Raises
    ConfigError naming the first bad setting in `.args[1]`. Returns the stored overrides, the
    new version and the overrides as they were. The caller commits."""
    values, version, row = await load(db)
    before = dict(values)
    for name, raw in changes.items():
        opt = BY_NAME.get(name)
        if opt is None:
            raise ConfigError("Not a setting that can be changed here.", name)
        if raw is None:
            values.pop(name, None)
            continue
        try:
            values[name] = validate(opt, raw)
        except ConfigError as exc:
            raise ConfigError(str(exc), name) from None
    version += 1
    stored = {"values": values, "version": version}
    if row is None:
        db.add(AppSetting(key=CONFIG_KEY, value=stored, updated_by=actor_id))
    else:
        row.value, row.updated_by = stored, actor_id
    return values, version, before


async def announce(redis, version: int) -> None:
    """Tell the other processes there is a new version. Best effort: they re-read anyway."""
    try:
        await redis.set(VERSION_KEY, version)
    except Exception:  # noqa: BLE001 - Valkey down: the periodic re-read catches up
        log.warning("Could not announce config version %s", version)


def _instance() -> str:
    return socket.gethostname()


async def report(redis, settings: Settings, service: str) -> None:
    """Say which version this process runs and what waits for its restart, for the Admin page."""
    st = _state(settings)
    body = {
        "service": service,
        "instance": _instance(),
        "version": st.version,
        "pending_restart": list(st.pending_restart),
        "applied_at": st.applied_at.isoformat() if st.applied_at else None,
    }
    try:
        await redis.set(
            f"{APPLIED_PREFIX}{service}:{_instance()}", json.dumps(body), ex=REPORT_TTL_S
        )
    except Exception:  # noqa: BLE001
        pass


async def reports(redis) -> list[dict]:
    """Every live process's report, by service then instance."""
    out = []
    try:
        keys = [k async for k in redis.scan_iter(f"{APPLIED_PREFIX}*")]
        values = await redis.mget(keys) if keys else []
    except Exception:  # noqa: BLE001
        return []
    for v in values:
        if v:
            try:
                out.append(json.loads(v))
            except ValueError:
                continue
    return sorted(out, key=lambda r: (r.get("service", ""), r.get("instance", "")))


async def load_and_apply(
    settings: Settings, sessionmaker: async_sessionmaker, service: str, *, starting: bool
) -> list[str]:
    async with sessionmaker() as db:
        values, version, _ = await load(db)
    return apply(settings, values, service, version=version, starting=starting)


async def _redis_version(redis) -> str | None:
    v = await redis.get(VERSION_KEY)
    return v.decode() if isinstance(v, bytes) else v


async def watch(settings: Settings, sessionmaker: async_sessionmaker, redis, service: str) -> None:
    """Keep `settings` in step with the stored overrides. Runs until cancelled; never raises."""
    seen: str | None = None
    last_read = time.monotonic()
    while True:
        try:
            current = await _redis_version(redis)
            if current != seen or time.monotonic() - last_read >= REREAD_EVERY_S:
                await load_and_apply(settings, sessionmaker, service, starting=False)
                seen, last_read = current, time.monotonic()
            await report(redis, settings, service)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Config check failed")
        await asyncio.sleep(CHECK_EVERY_S)


def preload(settings: Settings, service: str) -> None:
    """Apply the stored overrides before a worker's module finishes importing, so settings read
    at import (arq job timeouts, cron times) see them. Skipped inside a running event loop and
    in tests; any failure leaves the .env values, and the watcher applies live ones later."""
    if settings.env == "test":
        return
    try:
        asyncio.get_running_loop()
        return
    except RuntimeError:
        pass

    async def _go() -> list[str]:
        from nexus_common.db import make_engine, make_sessionmaker

        engine = make_engine(settings.database_url)
        try:
            async with asyncio.timeout(10):
                return await load_and_apply(
                    settings, make_sessionmaker(engine), service, starting=True
                )
        finally:
            await engine.dispose()

    try:
        asyncio.run(_go())
    except Exception as exc:  # noqa: BLE001 - start with .env values rather than not at all
        log.warning("Could not load stored configuration, using .env values: %s", exc)
