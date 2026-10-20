import {
  Activity,
  AlertTriangle,
  Bot,
  Brain,
  FolderOpen,
  Link2,
  LogOut,
  Menu,
  MessageSquarePlus,
  Sparkles,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate, useParams } from "react-router";
import { api } from "../lib/api";
import { hasRole } from "../lib/types";
import { useStore } from "../store/chat";
import { Orb } from "./Orb";

const navClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-2 rounded-md px-2 py-1.5 ${
    isActive ? "bg-edge text-white" : "text-slate-300 hover:bg-edge/50"
  }`;

export function Shell() {
  const user = useStore((s) => s.user)!;
  const chats = useStore((s) => s.chats);
  const setChats = useStore((s) => s.setChats);
  const removeChat = useStore((s) => s.removeChat);
  const jarvis = useStore((s) => s.jarvis);
  const connected = useStore((s) => s.connected);
  const waiting = useStore(
    (s) => Object.values(s.approvals).filter((a) => a.status === "pending").length,
  );
  const upsertApprovals = useStore((s) => s.upsertApprovals);
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const { chatId } = useParams();
  const { pathname } = useLocation();

  useEffect(() => {
    api.chats().then(setChats).catch(console.error);
  }, [setChats]);

  useEffect(() => setOpen(false), [pathname]);

  // Steps waiting for approval show as a badge wherever the user is.
  useEffect(() => {
    if (connected) api.approvals().then(upsertApprovals).catch(() => {});
  }, [connected, upsertApprovals]);

  const remove = async (id: string) => {
    if (!confirm("Delete this chat?")) return;
    await api.deleteChat(id);
    removeChat(id);
    if (id === chatId) navigate("/");
  };

  const signOut = async () => {
    await api.logout();
    location.href = "/login";
  };

  const spend = user.budget.spend_usd;
  return (
    <div className="flex h-full">
      <aside
        className={`fixed inset-y-0 left-0 z-30 flex w-72 flex-col border-r border-edge bg-panel transition-transform md:static md:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center gap-3 px-4 py-4">
          <Orb state={jarvis} />
          <span className="text-lg font-semibold tracking-wide">Jarvis</span>
          <span
            className={`ml-auto size-2 rounded-full ${connected ? "bg-emerald-400" : "bg-slate-600"}`}
            title={connected ? "Live" : "Reconnecting"}
          />
          <button className="md:hidden" onClick={() => setOpen(false)} aria-label="Close menu">
            <X size={18} />
          </button>
        </div>
        <button
          onClick={() => navigate("/")}
          disabled={!hasRole(user, "member")}
          className="mx-3 mb-2 flex items-center gap-2 rounded-lg border border-edge px-3 py-2 text-sm hover:bg-edge/60 disabled:opacity-40"
        >
          <MessageSquarePlus size={16} /> New chat
        </button>
        <nav className="flex-1 overflow-y-auto px-2">
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
        <div className="border-t border-edge p-3 text-sm">
          <NavLink to="/library" className={navClass}>
            <FolderOpen size={16} /> Library
          </NavLink>
          <NavLink to="/memory" className={navClass}>
            <Brain size={16} /> Memory
          </NavLink>
          <NavLink to="/agents" className={navClass}>
            <Bot size={16} /> Agents
            {waiting > 0 && (
              <span
                className="ml-auto rounded-full bg-rose-500 px-1.5 text-xs text-white"
                title="Steps waiting for your approval"
              >
                {waiting}
              </span>
            )}
          </NavLink>
          <NavLink to="/skills" className={navClass}>
            <Sparkles size={16} /> Skills
          </NavLink>
          <NavLink to="/connections" className={navClass}>
            <Link2 size={16} /> Connections
          </NavLink>
          {hasRole(user, "admin") && (
            <NavLink to="/people" className={navClass}>
              <Users size={16} /> People
            </NavLink>
          )}
          {hasRole(user, "admin") && (
            <NavLink to="/admin" className={navClass}>
              <Activity size={16} /> Admin
            </NavLink>
          )}
          <div className="mb-2" />
          <div className="flex items-center gap-2 px-2">
            <div className="min-w-0 flex-1">
              <div className="truncate">{user.name || user.email}</div>
              <div className="text-xs text-slate-500">
                {user.role} · ${spend != null ? spend.toFixed(2) : "–"} of ${user.budget.max_usd}
              </div>
            </div>
            <button onClick={signOut} className="text-slate-400 hover:text-white" aria-label="Sign out">
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
          <button onClick={() => setOpen(true)} aria-label="Open menu">
            <Menu size={20} />
          </button>
          <Orb state={jarvis} size={22} />
          <span className="font-semibold">Jarvis</span>
        </header>
        {user.notice && (
          <div className="flex items-center gap-2 border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-200">
            <AlertTriangle size={16} className="shrink-0" /> {user.notice}
          </div>
        )}
        <Outlet />
      </main>
    </div>
  );
}
