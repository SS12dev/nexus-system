import { useEffect, useState } from "react";

export const WIDE = "(min-width: 1024px)";

/** Whether the screen is laptop-wide (Tailwind's lg), following resizes. */
export function useWide(): boolean {
  const [wide, setWide] = useState(() => typeof matchMedia === "function" && matchMedia(WIDE).matches);
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const mq = matchMedia(WIDE);
    const change = () => setWide(mq.matches);
    mq.addEventListener("change", change);
    return () => mq.removeEventListener("change", change);
  }, []);
  return wide;
}
