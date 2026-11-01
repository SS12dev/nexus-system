import { Brain } from "lucide-react";
import { useAssistantName } from "../lib/assistant";
import { ENTITY_COLOR } from "../lib/entities";
import type { Recalled } from "../lib/types";
import { useStore } from "../store/chat";

const SHOWN = 5;

/** Under a reply: the people, projects and topics and how many memories it drew on. Tapping
 * lights them up in the universe. */
export function RecallChips({ recalled }: { recalled: Recalled }) {
  const assistant = useAssistantName();
  const showRecall = useStore((s) => s.showRecall);
  const { entities, memories } = recalled;
  if (!entities.length && !memories.length) return null;
  const extra = entities.length - SHOWN;
  return (
    <button
      onClick={() => showRecall(recalled)}
      className="mt-2 flex max-w-full flex-wrap items-center gap-1.5 rounded-lg text-left text-xs text-slate-400 hover:text-slate-200"
      title={`What ${assistant} remembered for this reply. Tap to light it up in your universe.`}
    >
      <span className="flex items-center gap-1 text-slate-500">
        <Brain size={13} /> Remembered
      </span>
      {entities.slice(0, SHOWN).map((e) => (
        <span key={e.id} className="flex items-center gap-1 rounded-full bg-panel px-2 py-0.5 text-slate-300">
          <span className="size-1.5 rounded-full" style={{ background: ENTITY_COLOR[e.type] ?? ENTITY_COLOR.other }} />
          {e.name}
        </span>
      ))}
      {extra > 0 && <span className="rounded-full bg-panel px-2 py-0.5">+{extra}</span>}
      {memories.length > 0 && (
        <span className="rounded-full bg-panel px-2 py-0.5">
          {memories.length} {memories.length === 1 ? "memory" : "memories"}
        </span>
      )}
    </button>
  );
}
