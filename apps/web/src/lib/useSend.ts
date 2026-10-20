import { useCallback, useState } from "react";
import { useNavigate } from "react-router";
import { useStore } from "../store/chat";
import { api } from "./api";

/** Send a message to a chat, creating the chat first when there is none (the home screen and
 * a new chat). Returns whether it was sent, so the composer knows to clear. */
export function useSend(chatId?: string) {
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const send = useCallback(
    async (content: string, fileIds: string[]): Promise<boolean> => {
      const { upsertChat, setMessages, addMessages } = useStore.getState();
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
    },
    [chatId, navigate],
  );
  return { send, error, setError };
}
