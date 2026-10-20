import { useEffect, useState } from "react";

export interface Perf {
  /** Phone mode: fewer particles, no bloom, lower pixel ratio and a 30 fps cap. */
  lite: boolean;
  /** The user asked the OS for less motion: no spin, no pulse, objects snap into place. */
  reducedMotion: boolean;
  /** Taller than wide: teams sit on a tighter band. */
  portrait: boolean;
  maxMemories: number;
  maxCrystals: number;
  stars: number;
}

function detect(): Perf {
  const mq = (q: string) => typeof matchMedia === "function" && matchMedia(q).matches;
  const nav = navigator as Navigator & { connection?: { saveData?: boolean }; deviceMemory?: number };
  const lite =
    (mq("(pointer: coarse)") && mq("(max-width: 1024px)")) ||
    (navigator.hardwareConcurrency ?? 8) <= 4 ||
    (nav.deviceMemory ?? 8) <= 4 ||
    !!nav.connection?.saveData;
  return {
    lite,
    reducedMotion: mq("(prefers-reduced-motion: reduce)"),
    portrait: mq("(orientation: portrait)"),
    maxMemories: lite ? 150 : 600,
    maxCrystals: lite ? 12 : 30,
    stars: lite ? 600 : 2500,
  };
}

export function usePerf(): Perf {
  const [perf, setPerf] = useState(detect);
  useEffect(() => {
    const queries = [
      "(prefers-reduced-motion: reduce)",
      "(pointer: coarse)",
      "(max-width: 1024px)",
      "(orientation: portrait)",
    ];
    const lists = queries.map((q) => matchMedia(q));
    const update = () => setPerf(detect());
    lists.forEach((l) => l.addEventListener("change", update));
    return () => lists.forEach((l) => l.removeEventListener("change", update));
  }, []);
  return perf;
}

/** Whether this browser can draw WebGL at all; without it the page shows the list view. */
export function hasWebGL(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") ?? c.getContext("webgl"));
  } catch {
    return false;
  }
}
