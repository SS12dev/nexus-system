import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router";
import { Shell } from "./components/Shell";
import { api, ApiError } from "./lib/api";
import { useEventSocket } from "./lib/socket";
import { hasRole } from "./lib/types";
import { AgentsPage } from "./pages/AgentsPage";
import { ChatPage } from "./pages/ChatPage";
import { ConnectionsPage } from "./pages/ConnectionsPage";
import { LibraryPage } from "./pages/LibraryPage";
import { LoginPage } from "./pages/LoginPage";
import { MemoryPage } from "./pages/MemoryPage";
import { PeoplePage } from "./pages/PeoplePage";
import { SkillsPage } from "./pages/SkillsPage";
import { useStore } from "./store/chat";

export function App() {
  const user = useStore((s) => s.user);
  const setUser = useStore((s) => s.setUser);
  const [loading, setLoading] = useState(true);
  const location = useLocation();

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch((e) => {
        if (!(e instanceof ApiError && e.status === 401)) console.error(e);
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, [setUser]);

  useEventSocket(!!user);

  if (loading) {
    return <div className="grid h-full place-items-center text-slate-500">Loading…</div>;
  }
  if (!user) {
    return location.pathname === "/login" ? (
      <LoginPage />
    ) : (
      <Navigate to={`/login${location.search}`} replace />
    );
  }

  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<ChatPage />} />
        <Route path="c/:chatId" element={<ChatPage />} />
        <Route path="library" element={<LibraryPage />} />
        <Route path="memory" element={<MemoryPage />} />
        <Route path="agents" element={<AgentsPage />} />
        <Route path="agents/:systemId" element={<AgentsPage />} />
        <Route path="skills" element={<SkillsPage />} />
        <Route path="connections" element={<ConnectionsPage />} />
        {hasRole(user, "admin") && <Route path="people" element={<PeoplePage />} />}
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
