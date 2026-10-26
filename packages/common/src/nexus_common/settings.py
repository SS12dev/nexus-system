from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    """Runtime configuration shared by every Nexus service, read from the environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    env: Literal["development", "production", "test"] = "development"

    # Logs: "text" for reading in a terminal, "json" (one object per line, with the request id)
    # for a log collector such as Loki, Datadog or CloudWatch.
    log_format: Literal["text", "json"] = "text"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://nexus:nexus@localhost:5432/nexus"
    redis_url: str = "redis://localhost:6379/0"

    # Model gateway. Every LLM call goes through LiteLLM using the nexus-* aliases.
    litellm_base_url: str = "http://localhost:4000"
    # Admin key for the proxy: used to mint and manage per-user virtual keys.
    litellm_master_key: str = ""
    # When false (e.g. a gateway without a database), every call uses litellm_api_key instead
    # of a per-user virtual key, and per-user budgets are not enforced by the gateway.
    litellm_virtual_keys: bool = True
    litellm_api_key: str = ""
    litellm_budget_duration: str = "30d"
    chat_model: str = "nexus-mini"

    # Budgets in USD per budget period, applied when a user's gateway key is created.
    default_budget_usd: float = 5.0
    owner_budget_usd: float = 50.0

    # Auth
    secret_key: str = Field(default="change-me", min_length=8)
    # Encrypts stored Google tokens: comma-separated, the first encrypts and any decrypts, so a
    # new key goes first and the old one is dropped later. Empty uses SECRET_KEY, as before.
    token_encryption_keys: str = ""
    session_cookie_name: str = "nexus_session"
    session_ttl_days: int = 30
    # Force the Secure flag on cookies; otherwise it follows the request scheme.
    session_cookie_secure: bool = False
    signup_policy: Literal["invite_only", "open"] = "invite_only"
    # With invite_only: someone whose Google sign-in is refused for lack of an invite may ask
    # for one from the login page. Admins see it on the People page and get an email.
    access_requests_enabled: bool = True
    # After a denial, how long before the same address may ask again.
    access_request_cooldown_days: int = 7
    # Open requests kept at once; beyond this new ones are refused until admins catch up.
    access_request_max_pending: int = 200
    # Who is emailed about a new request (comma separated). Empty: every active owner and admin.
    access_request_notify: str = ""

    oidc_issuer: str = "https://accounts.google.com"
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    # Google services Nexus asks for at sign-in, besides the user's name and email: any of
    # gmail, calendar, contacts, tasks, drive. Each is optional on Google's consent screen, so
    # people can untick them and still sign in. Empty asks for sign-in only. Used only when
    # the issuer is Google.
    google_services: str = "gmail,calendar,contacts,tasks"

    # Passwordless local sign-in for development: anyone who knows the token can sign in as
    # an invited (or the first) user. Never enable on a public hostname.
    dev_login_enabled: bool = False
    dev_login_token: str = ""

    # Comma separated origins allowed to open the WebSocket, besides the request's own host.
    allowed_origins: str = ""

    chat_history_limit: int = 40

    # Object storage (MinIO locally, any S3 API later) for uploads.
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "nexus"
    s3_secret_key: str = ""
    s3_bucket: str = "nexus"
    s3_region: str = "us-east-1"
    max_upload_mb: int = 50

    # Embeddings go through LiteLLM too. The default alias points at bge-m3 in Ollama on the
    # host, so they cost nothing; see infra/litellm/config.yaml.
    embedding_model: str = "nexus-embed"
    embedding_batch_size: int = 32

    # Memory. Extraction, summaries and the profile use the cheapest tier.
    memory_model: str = "nexus-nano"
    # Memories, past-chat summaries and document chunks put in the prompt each turn.
    memory_top_k: int = 8
    # Memories further than this (cosine distance) from the message are left out unless a word
    # of it is in them: short ones like "Wants help with cooking." sit 0.4 to 0.5 from
    # anything, while one really about the message is nearer 0.2 to 0.35.
    memory_max_distance: float = 0.4
    episode_top_k: int = 2
    chunk_top_k: int = 6
    # Attached documents shorter than this go into the prompt whole instead of as excerpts.
    inline_document_chars: int = 16000
    # A chat is summarised once it has been quiet this long.
    episode_idle_minutes: int = 20
    # Knowledge graph: the people, projects and topics memories are about, and how they relate.
    graph_enabled: bool = True
    # Recall also follows it: memories about what the question's entities link to, within
    # graph_hops, up to graph_extra_memories more per turn. Off until the recall check says so.
    graph_recall_enabled: bool = False
    graph_hops: int = 2
    graph_extra_memories: int = 4
    # Nightly memory upkeep (decay, merge, profile rebuild), in UTC. 21:30 UTC is 03:00 IST.
    memory_nightly_hour_utc: int = 21
    memory_nightly_minute_utc: int = 30

    # Skills library: shipped skills and instructions, read at startup (library/ in the repo).
    library_path: str = "library"

    # Agent systems. Nexus designs them with nexus-pro; each run gets its own LiteLLM key.
    design_model: str = "nexus-pro"
    # Before Nexus answers, this model decides whether the message is work for a team (empty
    # turns the router off). Messages shorter than route_min_chars ("hi", "thanks") skip it.
    route_model: str = "nexus-nano"
    route_min_chars: int = 15
    default_run_budget_usd: float = 0.50
    # Software teams (dev_project) plan, build, test and review in rounds, so they get more.
    dev_run_budget_usd: float = 3.00
    max_run_budget_usd: float = 5.00
    # Agents working at once in a parallel_map_reduce system or a flow, and items a map may fan
    # out to.
    max_parallel_agents: int = 4
    max_map_items: int = 8
    # Model calls one agent may make in one turn of a run before it has to stop.
    agent_max_steps: int = 12
    # Roles whose systems may use nexus-max for the lead agent when asked for the best model.
    max_tier_roles: str = "owner,admin"
    # After a run with at least this many tool calls, Nexus offers to save what it learned
    # as a skill (0 turns it off).
    learn_after_tool_calls: int = 15

    # Sandbox for agent code: no network, CPU and memory caps, one work folder per call.
    sandbox_url: str = "http://sandbox:8100"
    sandbox_token: str = ""
    sandbox_timeout_s: int = 120
    # Workbench for dev teams: a persistent git folder per project with Node, Python, git and
    # headless Chromium; internet only to npm, PyPI and GitHub.
    workbench_url: str = "http://workbench:8200"
    workbench_token: str = ""
    # Wall-clock time for one ws_run command unless the agent asks for longer, and the most
    # it may ask for.
    workbench_timeout_s: int = 300
    workbench_max_timeout_s: int = 900

    # GitHub: each software project gets a private repo in this organisation, written by the
    # Nexus GitHub App installed on it (docs/github-app.md). The key is the App's private key
    # (PEM), base64 on one line. Empty turns GitHub off: projects then stay in the workbench.
    github_apps_org: str = ""
    github_app_id: str = ""
    github_app_installation_id: str = ""
    github_app_private_key_b64: str = ""
    github_api_url: str = "https://api.github.com"

    # Deployer: builds software projects into containers on the apps network.
    deployer_url: str = "http://deployer:8300"
    deployer_token: str = ""
    # A deploy builds, starts, health-checks and screenshots; this is how long it may take.
    deploy_timeout_s: int = 1500
    # Apps a member may have running at once; the owner has no limit.
    member_app_limit: int = 3
    # Apps answer at <name>.<apps_domain>; nexus_url is where people sign in to open a private
    # one. reserved_hostnames: names apps may never take (comma separated), on top of nexus,
    # www, mail, api, admin and traefik, e.g. the names your other servers use.
    apps_domain: str = "apps.localhost"
    nexus_url: str = "https://nexus.servhub.cc"
    reserved_hostnames: str = ""
    # How long one sign-in to a private app lasts on that app's host.
    app_cookie_hours: int = 12

    @property
    def reserved_hostname_set(self) -> set[str]:
        return {h.strip().lower() for h in self.reserved_hostnames.split(",") if h.strip()}

    # Longest a single run_system or continue_run job may take.
    # Software teams plan, build, test, review and deploy, which can take most of it.
    run_job_timeout_s: int = 3600

    # Web search: tavily or brave when a key is set, otherwise DuckDuckGo (free, no key).
    web_search_provider: Literal["auto", "tavily", "brave", "duckduckgo"] = "auto"
    tavily_api_key: str = ""
    brave_api_key: str = ""

    # Email for agents (always needs approval). Any SMTP server, e.g. Gmail with an app password.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    # WhatsApp notices for routines, through Meta's WhatsApp Business Cloud API. A message
    # Nexus starts must use a template Meta approved (whatsapp_template, with two body
    # parameters: the routine's name and a short preview). Empty token turns WhatsApp off.
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_template: str = "nexus_routine"
    whatsapp_template_language: str = "en"
    whatsapp_api_url: str = "https://graph.facebook.com/v25.0"

    # Web push to phones and browsers (nexus_common/push.py). Make the pair once with
    # `uv run python -m nexus_common.push`; the subject is a mailto: or https: contact the push
    # services can reach (empty uses SMTP_FROM). Empty keys turn push off.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = ""

    # Routines: prompts that run on a schedule as their owner (nexus_common/routines.py).
    routines_enabled: bool = True
    routines_max_per_user: int = 10
    # The shortest gap a routine's schedule may have.
    routine_min_interval_minutes: int = 60
    # A routine is skipped when its owner has less than this left of their budget.
    routine_min_budget_usd: float = 0.05

    # Privacy: Langfuse keeps token counts, cost and timing for everyone, but replaces the
    # prompts and replies of everyone except owners with "redacted-by-litellm". The
    # self-review never reads Langfuse, so it keeps working (see improve.py).
    trace_masking: bool = True

    # Rate limits, counted in Redis so they hold across API replicas. Per user where signed in,
    # otherwise per client IP. 0 turns a limit off.
    rate_limit_enabled: bool = True
    rate_limit_api_per_minute: int = 600
    rate_limit_auth_per_minute: int = 20
    rate_limit_messages_per_minute: int = 20
    rate_limit_uploads_per_minute: int = 30
    rate_limit_runs_per_hour: int = 30

    # Shown as links on the Admin page.
    langfuse_url: str = "http://localhost:3000"

    # Backups (the backup service in compose.yaml). The API only reads its records and queues
    # "back up now"; this is how old the last good backup may be before the Admin page warns.
    backup_stale_hours: int = 36

    # Alerts (nexus_api/alerts.py): checked this often by one API replica (0 turns them off),
    # raised after two failing checks in a row, shown on the Admin page and emailed to the
    # owners when SMTP is set up. Thresholds are over the last 5 and 15 minutes.
    alert_interval_s: int = 60
    alert_api_errors_5m: int = 5
    alert_job_failures_15m: int = 3
    alert_email: bool = True
    # Model calls (from LiteLLM's spend logs): failed calls in 15 minutes, spend since midnight
    # UTC in dollars, and the share of a person's budget spent before it is raised. 0 is off.
    alert_llm_failures_15m: int = 5
    alert_daily_spend_usd: float = 10.0
    alert_budget_percent: int = 90

    # LiteLLM's own database, read for the Admin page's Models tab (spend logs: one row per
    # model call with its tokens, cost, timing and cache hit). Empty means the database called
    # "litellm" next to DATABASE_URL, which is how compose.yaml sets it up.
    litellm_database_url: str = ""

    # Self-review (nexus_orchestrator/improve.py): every `improve_every_days` days Nexus looks
    # at how it was used (counts and anonymous topics, never chats or names) and drafts
    # proposals for the owner to approve or deny on the Admin page. 0 turns the schedule off;
    # "Review now" still works. Topics need `improve_min_count` requests before they count.
    improve_every_days: int = 7
    improve_model: str = "nexus-mini"
    improve_topic_model: str = "nexus-nano"
    improve_read_requests: bool = True
    improve_min_count: int = 3
    improve_max_proposals: int = 5
    improve_max_requests: int = 400
    # Where approved code and tool proposals become pull requests, as owner/name. The Nexus
    # GitHub App must be installed on that repository with contents and pull requests write.
    # Empty means they are kept as an approved spec without a PR.
    improve_repo: str = ""
    improve_base_branch: str = "main"

    @property
    def github_enabled(self) -> bool:
        return bool(
            self.github_apps_org
            and self.github_app_id
            and self.github_app_installation_id
            and self.github_app_private_key_b64
        )

    @property
    def spend_logs_url(self) -> str:
        if self.litellm_database_url:
            return self.litellm_database_url
        return (
            make_url(self.database_url)
            .set(database="litellm")
            .render_as_string(hide_password=False)
        )

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host and (self.smtp_from or self.smtp_username))

    @property
    def push_enabled(self) -> bool:
        return bool(
            self.vapid_public_key
            and self.vapid_private_key
            and (self.vapid_subject or self.smtp_from)
        )

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.whatsapp_token and self.whatsapp_phone_number_id)

    @property
    def access_requests_open(self) -> bool:
        return self.access_requests_enabled and self.signup_policy == "invite_only"

    @property
    def access_request_notify_list(self) -> list[str]:
        return [e.strip().lower() for e in self.access_request_notify.split(",") if e.strip()]

    @property
    def max_tier_role_list(self) -> list[str]:
        return [r.strip() for r in self.max_tier_roles.split(",") if r.strip()]

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_client_id and self.oidc_client_secret)

    @property
    def google_service_list(self) -> list[str]:
        if "accounts.google.com" not in self.oidc_issuer:
            return []
        return [s.strip().lower() for s in self.google_services.split(",") if s.strip()]

    @property
    def dev_login_active(self) -> bool:
        return self.dev_login_enabled and len(self.dev_login_token) >= 8

    @property
    def allowed_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
