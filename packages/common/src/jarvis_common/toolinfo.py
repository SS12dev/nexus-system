"""The Tool Registry's catalogue: every tool Jarvis can give an agent, with its risk level. The
orchestrator holds the implementations; the API uses this list for the Agents page and grants.

Risk levels:
- read: looks at the user's own data or the public web.
- write: adds to the user's own library.
- exec: runs code in the sandbox. Needs the exec capability.
- outbound: acts outside Jarvis, such as sending email. Needs the outbound capability, and
  always asks the user first.
- destructive: deletes something of the user's. Always asks the user first.
"""

from dataclasses import dataclass
from typing import Literal

from jarvis_common.models import Capability, RoleName, User

Risk = Literal["read", "write", "exec", "outbound", "destructive"]

# Risks that always pause the run for the user's approval, whatever the spec says.
ALWAYS_APPROVE: set[str] = {"outbound", "destructive"}


@dataclass(frozen=True)
class ToolInfo:
    name: str
    title: str
    risk: Risk
    description: str
    # Jarvis itself can call it in a chat (not only agents in a system).
    jarvis: bool = True


TOOLS: list[ToolInfo] = [
    ToolInfo(
        "web_search", "Web search", "read", "Search the web; returns titles, links, snippets."
    ),
    ToolInfo("web_fetch", "Read a web page", "read", "Fetch a public web page as text."),
    ToolInfo("search_documents", "Search documents", "read", "Search the user's documents."),
    ToolInfo("read_document", "Read a document", "read", "Read a document's text by id."),
    ToolInfo("list_documents", "List documents", "read", "List the user's recent documents."),
    ToolInfo("search_memory", "Search memory", "read", "Search what Jarvis knows about the user."),
    ToolInfo(
        "save_file", "Save a file", "write", "Save text or Markdown as a file in the library."
    ),
    ToolInfo(
        "run_code",
        "Run code",
        "exec",
        "Run Python or shell in the sandbox (no network) with document libraries and skill "
        "scripts; files written to out/ are saved to the library.",
    ),
    ToolInfo(
        "send_email",
        "Send email",
        "outbound",
        "Send an email, optionally with library files attached. Asks the user first.",
    ),
    ToolInfo(
        "delete_document",
        "Delete a document",
        "destructive",
        "Delete a document from the user's library. Asks the user first.",
    ),
]

BY_NAME: dict[str, ToolInfo] = {t.name: t for t in TOOLS}

# What each risk needs beyond being a member.
RISK_CAPABILITY: dict[str, Capability | None] = {
    "read": None,
    "write": None,
    "exec": Capability.exec,
    "outbound": Capability.outbound,
    "destructive": None,
}


def capabilities(user: User, granted: set[Capability]) -> set[Capability]:
    """Owners and admins have every capability; members only those granted to them."""
    if user.has_role(RoleName.admin):
        return set(Capability)
    if not user.has_role(RoleName.member):
        return set()
    return set(granted)


def allowed_tools(user: User, granted: set[Capability]) -> set[str]:
    if not user.has_role(RoleName.member):
        return set()
    caps = capabilities(user, granted)
    return {t.name for t in TOOLS if (need := RISK_CAPABILITY[t.risk]) is None or need in caps}


def needs_approval(tool: str, extra: set[str] | list[str] = ()) -> bool:
    info = BY_NAME.get(tool)
    return (info is not None and info.risk in ALWAYS_APPROVE) or tool in set(extra)
