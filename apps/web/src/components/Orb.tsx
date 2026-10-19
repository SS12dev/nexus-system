import type { JarvisState } from "../lib/types";

export function Orb({ state, size = 28 }: { state: JarvisState; size?: number }) {
  return (
    <div
      className="orb shrink-0"
      data-state={state}
      style={{ width: size, height: size }}
      role="img"
      aria-label={`Jarvis is ${state}`}
    />
  );
}
