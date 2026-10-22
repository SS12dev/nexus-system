import type { AssistantState } from "../lib/types";
import { useAssistantName } from "../lib/assistant";

export function Orb({ state, size = 28 }: { state: AssistantState; size?: number }) {
  const assistant = useAssistantName();
  return (
    <div
      className="orb shrink-0"
      data-state={state}
      style={{ width: size, height: size }}
      role="img"
      aria-label={`${assistant} is ${state}`}
    />
  );
}
