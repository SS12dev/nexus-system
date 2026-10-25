import type { ScreenMode } from "./types";

/** When the person last typed in a message box. Something the assistant opens on the screen
 * waits as a notice instead of taking over while they're mid-sentence. */
let lastTyped = 0;

/** How long after a keystroke the screen stays theirs. */
export const TYPING_MS = 10_000;

export function markTyped(now = Date.now()) {
  lastTyped = now;
}

export function typedRecently(now = Date.now()): boolean {
  return now - lastTyped < TYPING_MS;
}

const MODE_KEY = "nexus.home.view";

/** The layout this device showed last, for the moment before the saved screen loads. */
export function cachedMode(): ScreenMode | null {
  try {
    const v = localStorage.getItem(MODE_KEY);
    return v === "orb" || v === "list" || v === "dashboard" ? v : null;
  } catch {
    return null;
  }
}

export function cacheMode(mode: ScreenMode) {
  try {
    localStorage.setItem(MODE_KEY, mode);
  } catch {
    /* private mode */
  }
}
