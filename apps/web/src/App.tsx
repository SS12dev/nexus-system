import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router";
import { Shell } from "./components/Shell";
import { api, ApiError } from "./lib/api";
import { APP_NAME } from "./lib/assistant";
import { browserTimezone } from "./lib/schedule";
import { useEventSocket } from "./lib/socket";
import { useTheme } from "./lib/theme";
import { hasRole, isOwner } from "./lib/types";
import { AdminPage } from "./pages/AdminPage";
import { AgentsPage } from "./pages/AgentsPage";
import { ChatPage } from "./pages/ChatPage";
import { SettingsPage } from "./pages/SettingsPage";
import { HomePage } from "./pages/HomePage";
import { OrbLayout } from "./pages/OrbLayout";
import { AppsPage } from "./pages/AppsPage";
import { LibraryPage } from "./pages/LibraryPage";
import { LoginPage } from "./pages/LoginPage";
import { MindPage, MindRedirect } from "./pages/MindPage";
import { OnboardingPage } from "./pages/OnboardingPage";
import { PeoplePage } from "./pages/PeoplePage";
import { RoutinesPage } from "./pages/RoutinesPage";
import { SkillsPage } from "./pages/SkillsPage";
import { TermsPage } from "./pages/TermsPage";
import { TrashPage } from "./pages/TrashPage";
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
  // Keeps <html data-theme> following the system while the choice is "system".
  useTheme();

  // A zone left at UTC (or none) is the server's default, not a choice: take this device's, so
  // "every day at 9 pm" means 9 pm where they are. Routines on the old zone move with it.
  const zone = user?.onboarded_at ? user.timezone || "UTC" : null;
  useEffect(() => {
    const device = browserTimezone();
    if (zone !== "UTC" || device === "UTC") return;
    api
      .updateMe({ timezone: device })
      .then(setUser)
      .catch((e) => console.warn("Couldn't set the time zone", e));
  }, [zone, setUser]);

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

  // The setup interview, once, after first sign-in (it names the assistant too).
  if (!user.onboarded_at) return <OnboardingPage />;
  // The terms changed since they agreed: ask again before anything else.
  if (user.terms_needed) return <TermsPage />;

  return (
    <Routes>
      <Route path="setup" element={<OnboardingPage />} />
      <Route element={<Shell />}>
        <Route element={<OrbLayout />}>
          <Route index element={<HomePage />} />
          <Route path="c/:chatId" element={<ChatPage />} />
        </Route>
        <Route path="library" element={<LibraryPage />} />
        <Route path="apps" element={<AppsPage />} />
        <Route path="mind" element={<MindPage />} />
        <Route path="memory" element={<MindRedirect from="memory" />} />
        <Route path="knowledge" element={<MindRedirect from="knowledge" />} />
        <Route path="routines" element={<RoutinesPage />} />
        <Route path="agents" element={<AgentsPage />} />
        <Route path="agents/:systemId" element={<AgentsPage />} />
        <Route path="skills" element={<SkillsPage />} />
        <Route path="trash" element={<TrashPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="connections" element={<Navigate to="/settings" replace />} />
        {hasRole(user, "admin") && <Route path="people" element={<PeoplePage />} />}
        {isOwner(user) && <Route path="admin" element={<AdminPage />} />}
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
