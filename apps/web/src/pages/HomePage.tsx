import { useEffect, useState } from "react";
import { Composer } from "../components/Composer";
import { api } from "../lib/api";
import { hasRole } from "../lib/types";
import { useSend } from "../lib/useSend";
import { useStore } from "../store/chat";
import { useAssistantName } from "../lib/assistant";

/** The home screen's chat dock, under the orb (see OrbLayout). Sending opens the chat
 * beside the orb. */
export function HomePage() {
  const assistant = useAssistantName();
  return <NewChatComposer placeholder={`Ask ${assistant} anything`} suggest />;
}

/** A composer that always starts a new chat: the home dock, and the box under the orb while
 * another chat is open, so a fresh question doesn't need a trip to New chat first. On the home
 * screen it also offers a few prompts from the person's profile packs. */
export function NewChatComposer({
  placeholder,
  suggest = false,
}: {
  placeholder: string;
  suggest?: boolean;
}) {
  const user = useStore((s) => s.user)!;
  const { send, error } = useSend();
  const canChat = hasRole(user, "member");
  const [prompts, setPrompts] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (suggest && canChat) api.suggestions().then(setPrompts).catch(() => setPrompts([]));
  }, [suggest, canChat]);

  const pick = async (prompt: string) => {
    setBusy(true);
    try {
      await send(prompt, []);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative z-10">
      {error && <p className="mx-auto max-w-3xl px-4 pb-2 text-sm text-rose-400">{error}</p>}
      {prompts.length > 0 && (
        <div className="mx-auto flex max-w-3xl flex-wrap justify-center gap-2 px-4 pb-2">
          {prompts.map((p) => (
            <button
              key={p}
              disabled={busy}
              onClick={() => pick(p)}
              className="rounded-full border border-edge bg-panel/70 px-3 py-1 text-xs text-slate-300 backdrop-blur hover:border-sky-400/60 hover:text-sky-200 disabled:opacity-50"
            >
              {p}
            </button>
          ))}
        </div>
      )}
      <Composer
        onSend={send}
        disabled={!canChat}
        placeholder={canChat ? placeholder : "Viewers can read chats but not send messages"}
      />
    </div>
  );
}
