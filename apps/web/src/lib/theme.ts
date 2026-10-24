import { useEffect, useState } from "react";

/** Light or dark: the person's choice on this device (Settings › Appearance), or the system's.
 * index.html applies it before first paint; this keeps it in step afterwards. */

export type ThemeChoice = "system" | "dark" | "light";
const KEY = "nexus.theme";
const COLORS = { dark: "#05070d", light: "#f4f6fb" };

export function savedTheme(): ThemeChoice {
  try {
    const t = localStorage.getItem(KEY);
    return t === "dark" || t === "light" ? t : "system";
  } catch {
    return "system";
  }
}

function resolve(choice: ThemeChoice): "dark" | "light" {
  if (choice !== "system") return choice;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function applyTheme(choice: ThemeChoice): void {
  const theme = resolve(choice);
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", COLORS[theme]);
}

/** The current choice and a setter that saves it; follows the system while on "system". */
export function useTheme(): [ThemeChoice, (t: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(savedTheme);
  useEffect(() => {
    applyTheme(choice);
    if (choice !== "system") return;
    const mq = window.matchMedia?.("(prefers-color-scheme: light)");
    const onChange = () => applyTheme("system");
    mq?.addEventListener("change", onChange);
    return () => mq?.removeEventListener("change", onChange);
  }, [choice]);
  const set = (t: ThemeChoice) => {
    try {
      if (t === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, t);
    } catch {
      /* private mode: the choice lasts this visit */
    }
    setChoice(t);
  };
  return [choice, set];
}
