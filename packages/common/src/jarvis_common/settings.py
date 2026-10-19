from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration shared by every Jarvis service, read from the environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["development", "production", "test"] = "development"

    database_url: str = "postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis"
    redis_url: str = "redis://localhost:6379/0"

    # Model gateway. Every LLM call goes through LiteLLM using the jarvis-* aliases.
    litellm_base_url: str = "http://localhost:4000"
    # Admin key for the proxy: used to mint and manage per-user virtual keys.
    litellm_master_key: str = ""
    # When false (e.g. a gateway without a database), every call uses litellm_api_key instead
    # of a per-user virtual key, and per-user budgets are not enforced by the gateway.
    litellm_virtual_keys: bool = True
    litellm_api_key: str = ""
    litellm_budget_duration: str = "30d"
    jarvis_chat_model: str = "jarvis-mini"

    # Budgets in USD per budget period, applied when a user's gateway key is created.
    default_budget_usd: float = 5.0
    owner_budget_usd: float = 50.0

    # Auth
    secret_key: str = Field(default="change-me", min_length=8)
    session_cookie_name: str = "jarvis_session"
    session_ttl_days: int = 30
    # Force the Secure flag on cookies; otherwise it follows the request scheme.
    session_cookie_secure: bool = False
    signup_policy: Literal["invite_only", "open"] = "invite_only"

    oidc_issuer: str = "https://accounts.google.com"
    oidc_client_id: str = ""
    oidc_client_secret: str = ""

    # Passwordless local sign-in for development: anyone who knows the token can sign in as
    # an invited (or the first) user. Never enable on a public hostname.
    dev_login_enabled: bool = False
    dev_login_token: str = ""

    # Comma separated origins allowed to open the WebSocket, besides the request's own host.
    allowed_origins: str = ""

    chat_history_limit: int = 40

    # Object storage (MinIO locally, any S3 API later) for uploads.
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "jarvis"
    s3_secret_key: str = ""
    s3_bucket: str = "jarvis"
    s3_region: str = "us-east-1"
    max_upload_mb: int = 50

    # Embeddings go through LiteLLM too. The default alias points at bge-m3 in Ollama on the
    # host, so they cost nothing; see infra/litellm/config.yaml.
    embedding_model: str = "jarvis-embed"
    embedding_batch_size: int = 32

    # Memory. Extraction, summaries and the profile use the cheapest tier.
    memory_model: str = "jarvis-nano"
    # Memories, past-chat summaries and document chunks put in the prompt each turn.
    memory_top_k: int = 8
    episode_top_k: int = 2
    chunk_top_k: int = 6
    # Attached documents shorter than this go into the prompt whole instead of as excerpts.
    inline_document_chars: int = 16000
    # A chat is summarised once it has been quiet this long.
    episode_idle_minutes: int = 20
    # Nightly memory upkeep (decay, merge, profile rebuild), in UTC. 21:30 UTC is 03:00 IST.
    memory_nightly_hour_utc: int = 21
    memory_nightly_minute_utc: int = 30

    # Skills library: shipped skills and instructions, read at startup (library/ in the repo).
    library_path: str = "library"

    # Agent systems. Jarvis designs them with jarvis-pro; each run gets its own LiteLLM key.
    design_model: str = "jarvis-pro"
    default_run_budget_usd: float = 0.50
    max_run_budget_usd: float = 5.00
    # Agents working at once in a parallel_map_reduce system, and items it may fan out to.
    max_parallel_agents: int = 4
    max_map_items: int = 8
    # Model calls one agent may make in one turn of a run before it has to stop.
    agent_max_steps: int = 12
    # Roles whose systems may use jarvis-max for the lead agent when asked for the best model.
    max_tier_roles: str = "owner,admin"
    # After a run with at least this many tool calls, Jarvis offers to save what it learned
    # as a skill (0 turns it off).
    learn_after_tool_calls: int = 15

    # Sandbox for agent code: no network, CPU and memory caps, one work folder per call.
    sandbox_url: str = "http://sandbox:8100"
    sandbox_token: str = ""
    sandbox_timeout_s: int = 120

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

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host and (self.smtp_from or self.smtp_username))

    @property
    def max_tier_role_list(self) -> list[str]:
        return [r.strip() for r in self.max_tier_roles.split(",") if r.strip()]

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_client_id and self.oidc_client_secret)

    @property
    def dev_login_active(self) -> bool:
        return self.dev_login_enabled and len(self.dev_login_token) >= 8

    @property
    def allowed_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
