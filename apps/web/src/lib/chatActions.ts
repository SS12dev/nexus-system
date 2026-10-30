import { useCallback } from "react";
import { useNavigate, useParams } from "react-router";
import { useStore } from "../store/chat";
import { api } from "./api";
import { useWide } from "./media";
import { useSend } from "./useSend";

/** What the app's buttons (and custom widgets' app actions) do with chats: fork the open one,
 * start a new one, or ask the assistant something. A chat they make opens beside the open one
 * on a laptop, as a second chat window, and in its place on a phone or from the home screen. */
export function useChatActions() {
  const { chatId } = useParams();
  const navigate = useNavigate();
  const wide = useWide();
  const { send } = useSend(chatId);

  const show = useCallback(
    (id: string) => {
      if (wide && chatId && id !== chatId) useStore.getState().openSideChat(id);
      else navigate(`/c/${id}`);
    },
    [wide, chatId, navigate],
  );

  /** A copy of a chat (the open one by default) so far, opened as a second chat. */
  const fork = useCallback(
    async (id: string | undefined = chatId) => {
      if (!id) throw new Error("Open a chat first, then fork it");
      const chat = await api.forkChat(id);
      useStore.getState().upsertChat(chat);
      show(chat.id);
      return chat;
    },
    [chatId, show],
  );

  /** A new chat in a second window, asking `text` there first when given. */
  const newChat = useCallback(
    async (text = "") => {
      const { upsertChat, setMessages, addMessages } = useStore.getState();
      const chat = await api.createChat();
      upsertChat(chat);
      setMessages(chat.id, []);
      if (text.trim()) {
        const res = await api.send(chat.id, text.trim(), []);
        upsertChat(res.chat);
        addMessages(chat.id, [res.user_message, res.assistant_message]);
      }
      show(chat.id);
      return chat;
    },
    [show],
  );

  /** Ask the assistant in the open chat, or in a new one from the home screen. */
  const ask = useCallback(
    async (text: string) => {
      if (!text.trim()) throw new Error("There's nothing to ask");
      if (!(await send(text.trim(), []))) throw new Error("Couldn't send it; is the chat still answering?");
    },
    [send],
  );

  return { chatId: chatId ?? null, fork, newChat, ask, show };
}
