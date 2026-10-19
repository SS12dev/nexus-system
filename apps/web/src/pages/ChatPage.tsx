import { useEffect, useMemo, useRef, useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { isLive } from "../components/RunControls";
import { SystemCard } from "../components/SystemCard";
import { useNavigate, useParams } from "react-router";
import { Composer } from "../components/Composer";
import { MessageView } from "../components/MessageView";
import { Orb } from "../components/Orb";
import { api } from "../lib/api";
import { hasRole, type Message } from "../lib/types";
import { useStore } from "../store/chat";

const EMPTY: Message[] = [];

export function ChatPage() {
  const { chatId } = useParams();
  const navigate = useNavigate();
  const user = useStore((s) => s.user)!;
  const messages = useStore((s) => (chatId ? (s.messages[chatId] ?? EMPTY) : EMPTY));
  const connected = useStore((s) => s.connected);
  const jarvis = useStore((s) => s.jarvis);
  const activity = useStore((s) => s.activity);
  const allSystems = useStore((s) => s.systems);
  const allApprovals = useStore((s) => s.approvals);
  const { setMessages, mergeFetched, addMessages, upsertChat, upsertSystems, upsertApprovals } =
    useStore.getState();
  const [error, setError] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  // Load (or reload after a reconnect, in case events were missed) the open chat.
  useEffect(() => {
    setError("");
    if (!chatId) return;
    api
      .messages(chatId)
      .then((list) => mergeFetched(chatId, list))
      .catch(() => navigate("/", { replace: true }));
    // Teams started from this chat, and steps waiting for approval.
    api.systems({ chat_id: chatId }).then(upsertSystems).catch(() => {});
    api.approvals(chatId).then(upsertApprovals).catch(() => {});
  }, [chatId, connected, navigate, mergeFetched, upsertSystems, upsertApprovals]);

  const systems = useMemo(
    () =>
      Object.values(allSystems)
        .filter((s) => (s.run?.chat_id ?? s.chat_id) === chatId && s.run)
        .sort((a, b) => (a.run!.created_at < b.run!.created_at ? -1 : 1)),
    [allSystems, chatId],
  );
  const approvals = useMemo(
    () => Object.values(allApprovals).filter((a) => a.chat_id === chatId && a.status === "pending"),
    [allApprovals, chatId],
  );
  const liveSystems = systems.filter((s) => isLive(s.run));

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, liveSystems.length, approvals.length]);

  const send = async (content: string, fileIds: string[]): Promise<boolean> => {
    setError("");
    try {
      let id = chatId;
      if (!id) {
        const chat = await api.createChat();
        upsertChat(chat);
        setMessages(chat.id, []);
        id = chat.id;
        navigate(`/c/${id}`);
      }
      const res = await api.send(id, content, fileIds);
      upsertChat(res.chat);
      addMessages(id, [res.user_message, res.assistant_message]);
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  };

  const canChat = hasRole(user, "member");
  const busy = messages.some((m) => m.status === "streaming");
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <div className="grid h-full place-items-center px-6 text-center">
            <div>
              <div className="mb-5 flex justify-center">
                <Orb state={jarvis} size={120} />
              </div>
              <p className="text-lg text-slate-300">
                Hi{user.name ? ` ${user.name.split(" ")[0]}` : ""}, what can I do for you?
              </p>
              {canChat && (
                <p className="mt-2 text-sm text-slate-500">
                  Attach or drop PDFs, Word, PowerPoint or Excel files to ask about them.
                </p>
              )}
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
            {messages.map((m) => (
              <MessageView key={m.id} message={m} activity={activity} />
            ))}
            {liveSystems.map((s) => (
              <SystemCard key={s.id} system={s} />
            ))}
            {approvals.map((a) => (
              <ApprovalCard key={a.id} approval={a} systemName={allSystems[a.system_id]?.name} />
            ))}
            <div ref={bottom} />
          </div>
        )}
      </div>
      {error && <p className="mx-auto max-w-3xl px-4 pb-2 text-sm text-rose-400">{error}</p>}
      <Composer
        key={chatId ?? "new"}
        chatId={chatId}
        onSend={send}
        disabled={!canChat || busy}
        placeholder={canChat ? undefined : "Viewers can read chats but not send messages"}
      />
    </div>
  );
}
