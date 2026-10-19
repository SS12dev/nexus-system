import { useEffect } from "react";
import { useStore } from "../store/chat";
import type { ServerEvent } from "./types";

/** Keep one WebSocket open while signed in, reconnecting with backoff. */
export function useEventSocket(enabled: boolean) {
  const handleEvent = useStore((s) => s.handleEvent);
  const setConnected = useStore((s) => s.setConnected);

  useEffect(() => {
    if (!enabled) return;
    let socket: WebSocket | null = null;
    let retry = 0;
    let stopped = false;
    let pingTimer: number | undefined;
    let retryTimer: number | undefined;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${proto}://${location.host}/ws`);
      socket.onopen = () => {
        retry = 0;
        setConnected(true);
        pingTimer = window.setInterval(() => socket?.send("ping"), 25_000);
      };
      socket.onmessage = (e) => {
        try {
          handleEvent(JSON.parse(e.data) as ServerEvent);
        } catch {
          /* ignore malformed frames */
        }
      };
      socket.onclose = () => {
        setConnected(false);
        window.clearInterval(pingTimer);
        if (stopped) return;
        const delay = Math.min(30_000, 500 * 2 ** retry++);
        retryTimer = window.setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      stopped = true;
      window.clearInterval(pingTimer);
      window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [enabled, handleEvent, setConnected]);
}
