import { Composer } from "../components/Composer";
import { hasRole } from "../lib/types";
import { useSend } from "../lib/useSend";
import { useStore } from "../store/chat";
import { useAssistantName } from "../lib/assistant";

/** The home screen's chat dock, under the orb (see OrbLayout). Sending opens the chat
 * beside the orb. */
export function HomePage() {
  const assistant = useAssistantName();
  const user = useStore((s) => s.user)!;
  const { send, error } = useSend();
  const canChat = hasRole(user, "member");
  return (
    <div className="relative z-10">
      {error && <p className="mx-auto max-w-3xl px-4 pb-2 text-sm text-rose-400">{error}</p>}
      <Composer
        onSend={send}
        disabled={!canChat}
        placeholder={canChat ? `Ask ${assistant} anything` : "Viewers can read chats but not send messages"}
      />
    </div>
  );
}
