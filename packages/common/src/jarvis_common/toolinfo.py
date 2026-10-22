"""The Tool Registry's catalogue: every tool Jarvis can give an agent, with its risk level. The
orchestrator holds the implementations; the API uses this list for the Agents page and grants.

Risk levels:
- read: looks at the user's own data or the public web.
- write: adds to or changes the user's own data (library, own Gmail drafts and labels, own
  calendar events without guests, tasks).
- exec: runs code in the sandbox or the workbench. Needs the exec capability.
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
    # The Google service it acts on; offered only to users who granted it.
    service: str | None = None
    # Only the owner may have it, whatever their grants.
    owner_only: bool = False


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
        "save_link",
        "Save a link",
        "write",
        "Keep a link in the library (a GitHub repo, file or pull request, a Google Doc, an app, "
        "a page) with a preview, without copying what it points to.",
    ),
    ToolInfo(
        "run_code",
        "Run code",
        "exec",
        "Run Python or shell in the sandbox (no network) with document libraries and skill "
        "scripts; files written to out/ are saved to the library.",
    ),
    # The workbench: a dev team's project folder, kept for the whole run (agents only).
    ToolInfo(
        "ws_tree",
        "List project files",
        "read",
        "List the files in the project folder, with sizes.",
        jarvis=False,
    ),
    ToolInfo(
        "ws_read",
        "Read a project file",
        "read",
        "Read a text file from the project folder, with line numbers.",
        jarvis=False,
    ),
    ToolInfo(
        "ws_write",
        "Write a project file",
        "write",
        "Create or overwrite a file in the project folder.",
        jarvis=False,
    ),
    ToolInfo(
        "ws_edit",
        "Edit a project file",
        "write",
        "Replace an exact piece of text in a project file.",
        jarvis=False,
    ),
    ToolInfo(
        "ws_run",
        "Run a command",
        "exec",
        "Run a shell command in the project folder: npm, uv, git, Playwright with headless "
        "Chromium. Internet reaches only npm, PyPI and GitHub.",
        jarvis=False,
    ),
    ToolInfo(
        "ws_diff",
        "See project changes",
        "read",
        "Show what changed in the project since the last commit, or since a branch or commit.",
        jarvis=False,
    ),
    ToolInfo(
        "ws_commit",
        "Commit project changes",
        "write",
        "Commit every change in the project folder to its git history with a message.",
        jarvis=False,
    ),
    ToolInfo(
        "app_visibility",
        "Share an app",
        "write",
        "Make one of the user's running apps public (anyone with the link) or private (only "
        "them, after signing in to Jarvis).",
    ),
    ToolInfo(
        "github_transfer",
        "Move a project to GitHub",
        "outbound",
        "Transfer a software project's repo from Jarvis's GitHub organisation to someone's own "
        "GitHub account; GitHub emails them to accept it. Asks the user first.",
        jarvis=False,
        owner_only=True,
    ),
    ToolInfo(
        "send_email",
        "Send email",
        "outbound",
        "Send an email from Jarvis's own mail account (SMTP), optionally with library files "
        "attached. Prefer gmail_send when the user has Gmail connected. Asks the user first.",
        jarvis=False,
    ),
    ToolInfo(
        "delete_document",
        "Delete a document",
        "destructive",
        "Delete a document from the user's library. Asks the user first.",
        jarvis=False,
    ),
    ToolInfo(
        "gmail_search",
        "Search Gmail",
        "read",
        "Search the user's Gmail with Gmail search syntax; returns senders, subjects, snippets.",
        service="gmail",
    ),
    ToolInfo("gmail_read", "Read an email", "read", "Read one email by id.", service="gmail"),
    ToolInfo(
        "gmail_draft",
        "Draft an email",
        "write",
        "Save an email or reply as a draft in the user's Gmail, without sending it.",
        service="gmail",
    ),
    ToolInfo(
        "gmail_update_labels",
        "Label email",
        "write",
        "Archive, mark read, star or label an email in the user's Gmail.",
        service="gmail",
    ),
    ToolInfo(
        "gmail_send",
        "Send from Gmail",
        "outbound",
        "Send an email or reply from the user's own Gmail, optionally with library files "
        "attached. Asks the user first.",
        jarvis=False,
        service="gmail",
    ),
    ToolInfo(
        "calendar_list_events",
        "Read calendar",
        "read",
        "List events on the user's Google Calendar for a time range.",
        service="calendar",
    ),
    ToolInfo(
        "calendar_create_event",
        "Add calendar event",
        "write",
        "Add an event without guests to the user's calendar.",
        service="calendar",
    ),
    ToolInfo(
        "calendar_update_event",
        "Change calendar event",
        "write",
        "Change the time, title or notes of a calendar event that has no other guests.",
        service="calendar",
    ),
    ToolInfo(
        "calendar_invite",
        "Send calendar invite",
        "outbound",
        "Create an event and email invitations to guests. Asks the user first.",
        jarvis=False,
        service="calendar",
    ),
    ToolInfo(
        "calendar_delete_event",
        "Delete calendar event",
        "destructive",
        "Delete an event from the user's calendar, telling any guests. Asks the user first.",
        jarvis=False,
        service="calendar",
    ),
    ToolInfo(
        "contacts_search",
        "Search contacts",
        "read",
        "Find people in the user's Google Contacts with their emails and phones.",
        service="contacts",
    ),
    ToolInfo(
        "tasks_list",
        "List tasks",
        "read",
        "List the user's Google Tasks.",
        service="tasks",
    ),
    ToolInfo("tasks_add", "Add a task", "write", "Add a Google Task.", service="tasks"),
    ToolInfo(
        "tasks_complete",
        "Complete a task",
        "write",
        "Mark a Google Task as done.",
        service="tasks",
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
    owner = user.role == RoleName.owner
    return {
        t.name
        for t in TOOLS
        if ((need := RISK_CAPABILITY[t.risk]) is None or need in caps)
        and (owner or not t.owner_only)
    }


def connected_only(names: set[str], services: set[str] | list[str]) -> set[str]:
    """Drop tools for Google services the user hasn't granted."""
    keep = {None, *services}
    return {n for n in names if (info := BY_NAME.get(n)) is None or info.service in keep}


def needs_approval(tool: str, extra: set[str] | list[str] = ()) -> bool:
    info = BY_NAME.get(tool)
    return (info is not None and info.risk in ALWAYS_APPROVE) or tool in set(extra)
