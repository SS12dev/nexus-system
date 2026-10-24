import {
  Activity,
  AlertTriangle,
  AppWindow,
  Bot,
  Brain,
  CalendarClock,
  FolderOpen,
  LogOut,
  Menu,
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  Sparkles,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams } from "react-router";
import { api } from "../lib/api";
import { useAssistantName } from "../lib/assistant";
import { hasRole, isOwner } from "../lib/types";
import { useStore } from "../store/chat";
import { ErrorBoundary } from "./ErrorBoundary";
import { Orb } from "./Orb";
import { confirmAction } from "./Feedback";

const SIDEBAR_KEY = "nexus.sidebar";

function savedCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "collapsed";
  } catch {
    return false;
  }
}

export function Shell() {
  const user = useStore((s) => s.user)!;
  const assistant = useAssistantName();
  const chats = useStore((s) => s.chats);
  const setChats = useStore((s) => s.setChats);
  const removeChat = useStore((s) => s.removeChat);
  const state = useStore((s) => s.assistantState);
  const connected = useStore((s) => s.connected);
  const waiting = useStore(
    (s) => Object.values(s.approvals).filter((a) => a.status === "pending").length,
  );
  const upsertApprovals = useStore((s) => s.upsertApprovals);
  const pendingAccess = useStore((s) => s.pendingAccess) ?? 0;
  const [open, setOpen] = useState(false);
  // Laptop only: fold the sidebar down to a rail of icons. On phones it is a drawer anyway.
  const [collapsed, setCollapsed] = useState(savedCollapsed);
  const navigate = useNavigate();
  const { chatId } = useParams();
  const { pathname } = useLocation();

  // Routines post into chats of their own, made on the server: reload the list when one runs.
  const routinesVersion = useStore((s) => s.routinesVersion);
  useEffect(() => {
    api.chats().then(setChats).catch(console.error);
  }, [setChats, routinesVersion]);

  useEffect(() => setOpen(false), [pathname]);

  // Steps waiting for approval show as a badge wherever the user is.
  useEffect(() => {
    if (connected) api.approvals().then(upsertApprovals).catch(() => {});
  }, [connected, upsertApprovals]);

  // So do access requests, for owners and admins. Events keep the count live; this catches up
  // after the socket was down.
  const isAdmin = hasRole(user, "admin");
  const setPendingAccess = useStore((s) => s.setPendingAccess);
  useEffect(() => {
    if (connected && isAdmin)
      api.accessRequests().then((r) => setPendingAccess(r.pending)).catch(() => {});
  }, [connected, isAdmin, setPendingAccess]);

  const remove = async (id: string) => {
    const ok = await confirmAction({ title: "Delete this chat?", confirmLabel: "Delete", danger: true });
    if (!ok) return;
    await api.deleteChat(id);
    removeChat(id);
    if (id === chatId) navigate("/");
  };

  const signOut = async () => {
    await api.logout();
    location.href = "/login";
  };

  const toggleCollapsed = () => {
    setCollapsed(!collapsed);
    try {
      localStorage.setItem(SIDEBAR_KEY, collapsed ? "open" : "collapsed");
    } catch {
      /* private mode */
    }
  };

  // When folded, labels stay for screen readers but only the icons show.
  const label = collapsed ? "md:sr-only" : "";
  const navClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-2 rounded-md px-2 py-1.5 ${collapsed ? "md:justify-center" : ""} ${
      isActive ? "bg-edge text-white" : "text-slate-300 hover:bg-edge/50"
    }`;
  const tip = (text: string) => (collapsed ? text : undefined);

  const spend = user.budget.spend_usd;
  return (
    <div className="flex h-full">
      <aside
        className={`fixed inset-y-0 left-0 z-30 flex w-72 flex-col border-r border-edge bg-panel transition-[transform,width] md:static md:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        } ${collapsed ? "md:w-16" : ""}`}
      >
        <div
          className={`flex items-center gap-3 px-4 py-4 ${collapsed ? "md:flex-col md:gap-3 md:px-0" : ""}`}
        >
          <Link
            to="/"
            className="flex min-w-0 items-center gap-3 rounded-md"
            title="Home"
            aria-label={`${assistant} home`}
          >
            <span className="relative">
              <Orb state={state} />
              {collapsed && (
                <span
                  className={`absolute -right-0.5 -top-0.5 hidden size-2 rounded-full ring-2 ring-panel md:block ${connected ? "bg-emerald-400" : "bg-slate-600"}`}
                />
              )}
            </span>
            <span className={`text-lg font-semibold tracking-wide truncate ${label}`}>{assistant}</span>
          </Link>
          <span
            className={`ml-auto size-2 rounded-full ${connected ? "bg-emerald-400" : "bg-slate-600"} ${collapsed ? "md:hidden" : ""}`}
            title={connected ? "Live" : "Reconnecting"}
          />
          <button
            onClick={toggleCollapsed}
            className="hidden text-slate-400 hover:text-white md:block"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
          </button>
          <button className="md:hidden" onClick={() => setOpen(false)} aria-label="Close menu">
            <X size={18} />
          </button>
        </div>
        <button
          onClick={() => navigate("/")}
          disabled={!hasRole(user, "member")}
          title={tip("New chat")}
          className={`mx-3 mb-2 flex items-center gap-2 rounded-lg border border-edge px-3 py-2 text-sm hover:bg-edge/60 disabled:opacity-40 ${collapsed ? "md:mx-2 md:justify-center md:px-0" : ""}`}
        >
          <MessageSquarePlus size={16} className="shrink-0" />
          <span className={label}>New chat</span>
        </button>
        {/* Folded: the chat list goes; the space keeps the links at the bottom. */}
        {collapsed && <div className="hidden flex-1 md:block" />}
        <nav className={`flex-1 overflow-y-auto px-2 ${collapsed ? "md:hidden" : ""}`}>
          {chats.map((c) => (
            <div key={c.id} className="group flex items-center">
              <NavLink
                to={`/c/${c.id}`}
                className={({ isActive }) =>
                  `flex-1 truncate rounded-md px-3 py-2 text-sm ${
                    isActive ? "bg-edge text-white" : "text-slate-300 hover:bg-edge/50"
                  }`
                }
              >
                {c.title || "New chat"}
              </NavLink>
              {hasRole(user, "member") && (
                <button
                  onClick={() => remove(c.id)}
                  className="p-2 text-slate-500 opacity-0 group-hover:opacity-100 hover:text-rose-400"
                  aria-label="Delete chat"
                >
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          ))}
        </nav>
        <div className={`border-t border-edge p-3 text-sm ${collapsed ? "md:px-2" : ""}`}>
          <NavLink to="/library" className={navClass} title={tip("Library")}>
            <FolderOpen size={16} className="shrink-0" /> <span className={label}>Library</span>
          </NavLink>
          <NavLink to="/apps" className={navClass} title={tip("Apps")}>
            <AppWindow size={16} className="shrink-0" /> <span className={label}>Apps</span>
          </NavLink>
          <NavLink to="/routines" className={navClass} title={tip("Routines")}>
            <CalendarClock size={16} className="shrink-0" /> <span className={label}>Routines</span>
          </NavLink>
          <NavLink to="/memory" className={navClass} title={tip("Memory")}>
            <Brain size={16} className="shrink-0" /> <span className={label}>Memory</span>
          </NavLink>
          <NavLink to="/agents" className={(p) => `relative ${navClass(p)}`} title={tip("Agents")}>
            <Bot size={16} className="shrink-0" /> <span className={label}>Agents</span>
            {waiting > 0 && (
              <Badge collapsed={collapsed} title="Steps waiting for your approval">
                {waiting}
              </Badge>
            )}
          </NavLink>
          <NavLink to="/skills" className={navClass} title={tip("Skills")}>
            <Sparkles size={16} className="shrink-0" /> <span className={label}>Skills</span>
          </NavLink>
          <NavLink to="/settings" className={navClass} title={tip("Settings")}>
            <Settings size={16} className="shrink-0" /> <span className={label}>Settings</span>
          </NavLink>
          {hasRole(user, "admin") && (
            <NavLink to="/people" className={(p) => `relative ${navClass(p)}`} title={tip("People")}>
              <Users size={16} className="shrink-0" /> <span className={label}>People</span>
              {pendingAccess > 0 && (
                <Badge collapsed={collapsed} title={`${pendingAccess} waiting for access`}>
                  {pendingAccess}
                </Badge>
              )}
            </NavLink>
          )}
          {isOwner(user) && (
            <NavLink to="/admin" className={navClass} title={tip("Admin")}>
              <Activity size={16} className="shrink-0" /> <span className={label}>Admin</span>
            </NavLink>
          )}
          <div className="mb-2" />
          <div className={`flex items-center gap-2 px-2 ${collapsed ? "md:justify-center" : ""}`}>
            <div className={`min-w-0 flex-1 ${collapsed ? "md:hidden" : ""}`}>
              <div className="truncate">{user.name || user.email}</div>
              <div className="text-xs text-slate-500">
                {user.role} · ${spend != null ? spend.toFixed(2) : "–"} of ${user.budget.max_usd}
              </div>
            </div>
            <button
              onClick={signOut}
              className="text-slate-400 hover:text-white"
              aria-label="Sign out"
              title={collapsed ? `Sign out ${user.email}` : "Sign out"}
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>
      {open && (
        <div className="fixed inset-0 z-20 bg-black/50 md:hidden" onClick={() => setOpen(false)} />
      )}
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-edge px-4 py-3 md:hidden">
          <button
            onClick={() => setOpen(true)}
            aria-label={waiting + pendingAccess > 0 ? "Open menu, items need your attention" : "Open menu"}
            className="relative"
          >
            <Menu size={20} />
            {waiting + pendingAccess > 0 && (
              <span className="absolute -right-1 -top-1 size-2 rounded-full bg-rose-500 ring-2 ring-ink" />
            )}
          </button>
          <Link to="/" className="flex items-center gap-3" aria-label={`${assistant} home`}>
            <Orb state={state} size={22} />
            <span className="font-semibold">{assistant}</span>
          </Link>
        </header>
        {user.notice && (
          <div className="flex items-center gap-2 border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-200">
            <AlertTriangle size={16} className="shrink-0" /> {user.notice}
          </div>
        )}
        <ErrorBoundary resetKey={pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}

/** A count on a sidebar link, such as approvals or access requests waiting. */
function Badge({ collapsed, title, children }: { collapsed: boolean; title: string; children: ReactNode }) {
  return (
    <span
      className={`ml-auto rounded-full bg-rose-500 px-1.5 text-xs text-white ${
        collapsed ? "md:absolute md:-right-1 md:-top-1 md:ml-0" : ""
      }`}
      title={title}
    >
      {children}
    </span>
  );
}
