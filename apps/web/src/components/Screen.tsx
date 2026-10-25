import { useEffect, useRef } from "react";
import { api } from "../lib/api";
import { useAssistantName } from "../lib/assistant";
import { cacheMode, markTyped, typedRecently } from "../lib/screen";
import type { ScreenMode } from "../lib/types";
import { useStore } from "../store/chat";

/** Load the saved screen when signed in and after a reconnect, and keep this device's copy of
 * its layout current. */
export function useScreenLoader() {
  const connected = useStore((s) => s.connected);
  const setScreen = useStore((s) => s.setScreen);
  const mode = useStore((s) => s.screen.mode);
  const loaded = useRef(false);
  useEffect(() => {
    if (!connected) return;
    api
      .screen()
      .then((s) => {
        loaded.current = true;
        setScreen(s);
      })
      .catch(() => {});
  }, [connected, setScreen]);
  useEffect(() => {
    if (loaded.current) cacheMode(mode);
  }, [mode]);
  // Typing in any box (a message, a note) keeps the screen yours for a few seconds.
  useEffect(() => {
    const typed = () => markTyped();
    document.addEventListener("input", typed, true);
    return () => document.removeEventListener("input", typed, true);
  }, []);
}

/** Switch the layout by hand: at once here, saved for every device, and marked as the
 * person's own choice so the assistant leaves it alone for a while. */
export function setScreenMode(mode: ScreenMode) {
  const { screen, setScreen } = useStore.getState();
  setScreen({ ...screen, mode });
  cacheMode(mode);
  api
    .setScreenMode(mode)
    .then(setScreen)
    .catch(() => {});
}

/** Act on what the assistant does to the screen. A file it opens takes the orb's place when
 * it's for the chat on screen, the tab is visible and you haven't typed for a few seconds;
 * otherwise it waits as a toast. Clearing (or closing on another device) puts the result away. */
export function useScreenOps(chatId: string | undefined) {
  const op = useStore((s) => s.screenOp);
  const assistant = useAssistantName();
  const seen = useRef(op?.seq ?? 0);
  useEffect(() => {
    if (!op || op.seq === seen.current) return;
    seen.current = op.seq;
    const { viewer, openViewer, closeViewer, addLocalNotice } = useStore.getState();
    if (op.op === "clear" || op.op === "close") {
      if (viewer) closeViewer();
      return;
    }
    if (op.op !== "focus" || !op.file) return;
    const here = !op.chatId || op.chatId === chatId;
    if (here && document.visibilityState === "visible" && !typedRecently()) {
      openViewer(op.file, chatId ?? null);
      return;
    }
    addLocalNotice({
      kind: "info",
      title: `${assistant} wants to show you ${op.file.name}`,
      body: "",
      chat_id: op.chatId,
      run_id: null,
      url: op.chatId ? `/c/${op.chatId}` : "/",
      file: op.file,
      link: null,
    });
  }, [op, chatId, assistant]);
}

/** Whether the assistant just pointed at this widget, for a few seconds. */
export function useWidgetPointedAt(id: string): number {
  const op = useStore((s) => s.screenOp);
  return op?.op === "focus" && op.screen.focus?.kind === "widget" && op.screen.focus.id === id ? op.seq : 0;
}
