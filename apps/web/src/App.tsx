import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router";
import { Shell } from "./components/Shell";
import { api, ApiError } from "./lib/api";
import { APP_NAME } from "./lib/assistant";
import { useEventSocket } from "./lib/socket";
import { hasRole, isOwner } from "./lib/types";
import { AdminPage } from "./pages/AdminPage";
import { AgentsPage } from "./pages/AgentsPage";
import { ChatPage } from "./pages/ChatPage";
import { SettingsPage } from "./pages/SettingsPage";
import { HomePage } from "./pages/HomePage";
import { JarvisLayout } from "./pages/JarvisLayout";
import { AppsPage } from "./pages/AppsPage";
import { LibraryPage } from "./pages/LibraryPage";
import { LoginPage } from "./pages/LoginPage";
import { MemoryPage } from "./pages/MemoryPage";
import { NameAssistantPage } from "./pages/NameAssistantPage";
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

  const assistant = user?.assistant_name;
  useEffect(() => {
    document.title = assistant || APP_NAME;
  }, [assistant]);

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

  if (!user.assistant_name) return <NameAssistantPage />;

  return (
    <Routes>
      <Route element={<Shell />}>
        <Route element={<JarvisLayout />}>
          <Route index element={<HomePage />} />
          <Route path="c/:chatId" element={<ChatPage />} />
        </Route>
        <Route path="library" element={<LibraryPage />} />
        <Route path="apps" element={<AppsPage />} />
        <Route path="memory" element={<MemoryPage />} />
        <Route path="agents" element={<AgentsPage />} />
        <Route path="agents/:systemId" element={<AgentsPage />} />
        <Route path="skills" element={<SkillsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="connections" element={<Navigate to="/settings" replace />} />
        {hasRole(user, "admin") && <Route path="people" element={<PeoplePage />} />}
        {isOwner(user) && <Route path="admin" element={<AdminPage />} />}
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
