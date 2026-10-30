"""Web search and page fetching live in nexus_common.web, shared with the API (widgets' web
search and the reading view); this keeps the orchestrator's imports where they were."""

from nexus_common.web import *  # noqa: F403
from nexus_common.web import SearchResult, WebClient, WebError, html_to_text  # noqa: F401
