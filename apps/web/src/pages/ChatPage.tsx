import { Fragment, useEffect, useMemo, useRef } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { ClarificationCard } from "../components/ClarificationCard";
import { SystemCard } from "../components/SystemCard";
import { useNavigate, useParams } from "react-router";
import { Composer } from "../components/Composer";
import { MessageView } from "../components/MessageView";
import { api } from "../lib/api";
import { useSend } from "../lib/useSend";
import { hasRole, type AgentSystem, type Message } from "../lib/types";
import { useStore } from "../store/chat";
import { parseClarification } from "../lib/clarification";

const EMPTY: Message[] = [];

export function ChatPage() {
  const { chatId } = useParams();
  return <ChatView chatId={chatId} />;
}

/** A conversation: its messages, the teams it started and the composer. The main chat comes
 * from the address; a second one (side) sits beside it on a laptop, and closes instead of
 * going home when it can't be loaded. */
export function ChatView({ chatId, side = false }: { chatId?: string; side?: boolean }) {
  const navigate = useNavigate();
  const user = useStore((s) => s.user)!;
  const messages = useStore((s) => (chatId ? (s.messages[chatId] ?? EMPTY) : EMPTY));
  const connected = useStore((s) => s.connected);
  const activity = useStore((s) => s.activity);
  const allSystems = useStore((s) => s.systems);
  const allApprovals = useStore((s) => s.approvals);
  const { mergeFetched, upsertSystems, upsertApprovals } = useStore.getState();
  const { send, error, setError } = useSend(chatId);
  const bottom = useRef<HTMLDivElement>(null);

  // Load (or reload after a reconnect, in case events were missed) the open chat.
  useEffect(() => {
    setError("");
    if (!chatId) return;
    api
      .messages(chatId)
      .then((list) => mergeFetched(chatId, list))
      .catch(() => (side ? useStore.getState().openSideChat(null) : navigate("/", { replace: true })));
    // Teams started from this chat, and steps waiting for approval.
    api.systems({ chat_id: chatId }).then(upsertSystems).catch(() => {});
    api.approvals(chatId).then(upsertApprovals).catch(() => {});
  }, [chatId, side, connected, navigate, mergeFetched, upsertSystems, upsertApprovals, setError]);

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
  // Teams appear in the conversation where they started, right after the reply that started them.
  const timeline = useMemo(() => {
    const items: ({ at: string; message: Message } | { at: string; system: AgentSystem })[] = [
      ...messages.map((m) => ({ at: m.created_at, message: m })),
      ...systems.map((s) => ({ at: s.run!.created_at, system: s })),
    ];
    return items.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
  }, [messages, systems]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, systems.length, approvals.length]);

  const canChat = hasRole(user, "member");
  const busy = messages.some((m) => m.status === "streaming");
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <div className="grid h-full place-items-center px-6 text-center">
            <div>
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
          <div className="mx-auto max-w-3xl space-y-5 px-4 py-5">
            {timeline.map((item) => {
              if (!("message" in item)) return <SystemCard key={item.system.id} system={item.system} />;
              const clarification = parseClarification(item.message.content);
              const messageIndex = messages.findIndex((message) => message.id === item.message.id);
              const answered = messages
                .slice(messageIndex + 1)
                .some((message) => message.role === "user");
              return (
                <Fragment key={item.message.id}>
                  <MessageView message={item.message} activity={activity} />
                  {item.message.role === "assistant" &&
                    item.message.status === "complete" &&
                    clarification?.questions.length ? (
                    <ClarificationCard
                      questions={clarification.questions}
                      disabled={busy || answered}
                      onSubmit={(answers) => send(answers.join("\n\n"), [])}
                    />
                  ) : null}
                </Fragment>
              );
            })}
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
