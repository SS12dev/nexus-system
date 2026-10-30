import { useEffect, useState, type ReactNode } from "react";
import { ArrowLeftRight, ExternalLink, GitBranch, X } from "lucide-react";
import { Outlet, useNavigate, useParams } from "react-router";
import { FilePreview } from "../components/FilePreview";
import { useAutoOpenResult } from "../components/Notices";
import { OrbStage } from "../components/OrbStage";
import { useScreenOps } from "../components/Screen";
import { api } from "../lib/api";
import { useStore } from "../store/chat";
import { useAssistantName } from "../lib/assistant";
import { NewChatComposer } from "./HomePage";
import { ChatView } from "./ChatPage";
import { useWide } from "../lib/media";
import { useChatActions } from "../lib/chatActions";

const ORB_KEY = "nexus.chat.orb";

function savedOrbOpen(): boolean {
  try {
    return localStorage.getItem(ORB_KEY) !== "hidden";
  } catch {
    return true;
  }
}

/** Home and chats share one orb, so it keeps running (camera, teams mid-animation) when a
 * message opens a chat. On the home screen the orb fills the page over the composer; in a
 * chat it sits on the left with the chat on the right, or above the chat on phones, and can
 * be folded away. On laptops an open chat also keeps a new-chat box under the orb, so a
 * fresh question starts its own chat without going through New chat. Phones skip it: the
 * orb strip is too short, and two message boxes on one small screen would be confusing.
 *
 * A team's result (a 3D model, an image, a report) opens in the orb's place on laptops and as
 * a panel over everything on phones: by itself when it lands for the chat that's open, or
 * from a notice, or when you ask the assistant to show you something. Closing it brings the orb
 * back.
 *
 * A second chat can sit beside the open one on a laptop: a fork of it (the branch button in its
 * title bar, or a widget's button), or a new chat a widget started. It has its own title bar to
 * swap it with the main chat, pop it out into a window of its own, or close it. */
export function OrbLayout() {
  const { chatId } = useParams();
  const inChat = !!chatId;
  const [orbOpen, setOrbOpen] = useState(savedOrbOpen);
  const assistant = useAssistantName();
  const wide = useWide();
  const viewer = useStore((s) => s.viewer);
  const closeViewer = useStore((s) => s.closeViewer);
  useAutoOpenResult(chatId);
  useScreenOps(chatId);

  // A result belongs to its chat: moving to another chat (or home) puts it away.
  const showing = viewer && viewer.chatId === (chatId ?? null) ? viewer : null;
  useEffect(() => {
    if (viewer && viewer.chatId !== (chatId ?? null)) closeViewer();
  }, [chatId, viewer, closeViewer]);

  // Closed by hand: it stays closed on every device, and the assistant won't reopen it unasked.
  const close = () => {
    if (viewer) api.closeOnScreen("file", viewer.file.id).catch(() => {});
    closeViewer();
  };

  // The orb stays mounted under a result, so closing it finds the scene as it was.
  return (
    <>
      <OrbLayoutBody
        inChat={inChat}
        orbOpen={orbOpen || (!!showing && wide)}
        setOrbOpen={setOrbOpen}
        assistant={assistant}
        result={showing && wide ? <FilePreview inline file={showing.file} onClose={close} /> : null}
        chatId={chatId}
      />
      {showing && !wide && <FilePreview file={showing.file} onClose={close} />}
    </>
  );
}

function OrbLayoutBody({
  inChat,
  orbOpen,
  setOrbOpen,
  assistant,
  result = null,
  chatId,
}: {
  inChat: boolean;
  orbOpen: boolean;
  setOrbOpen: (open: boolean) => void;
  assistant: string;
  /** A result shown in the orb's place. */
  result?: ReactNode;
  chatId?: string;
}) {
  const navigate = useNavigate();
  const title = useStore((s) => s.chats.find((c) => c.id === chatId)?.title);
  const wide = useWide();
  const side = useStore((s) => s.sideChat);
  const openSideChat = useStore((s) => s.openSideChat);
  const { fork } = useChatActions();
  const [forking, setForking] = useState(false);
  const second = wide && inChat && side && side !== chatId ? side : null;
  useEffect(() => {
    // Opened as the main chat (swapped, or from the list): it's no longer the second one.
    if (side && side === chatId) openSideChat(null);
  }, [side, chatId, openSideChat]);

  const forkOpen = async () => {
    setForking(true);
    try {
      await fork();
    } catch {
      /* the chat went away meanwhile */
    } finally {
      setForking(false);
    }
  };

  /** Closing the main chat while a second is open makes the second the main one. */
  const closeMain = () => {
    if (second) {
      openSideChat(null);
      navigate(`/c/${second}`);
    } else navigate("/");
  };

  const collapse = (collapsed: boolean) => {
    setOrbOpen(!collapsed);
    try {
      localStorage.setItem(ORB_KEY, collapsed ? "hidden" : "shown");
    } catch {
      /* private mode */
    }
  };

  // min-w-0: beside two chats the orb gives way rather than pushing the chats off the screen.
  const stage = !inChat ? "flex-1" : orbOpen ? "h-[34vh] shrink-0 lg:h-auto lg:min-w-0 lg:flex-1" : "";
  // Two chats side by side share the room the one had, so the orb keeps some of it.
  const width = second ? "lg:w-[360px] xl:w-[420px] 2xl:w-[500px]" : "lg:w-[440px] xl:w-[520px]";
  const panel = !inChat
    ? "shrink-0"
    : orbOpen
      ? `flex min-h-0 flex-1 flex-col border-t border-edge ${width} lg:flex-none lg:border-l lg:border-t-0`
      : "flex min-h-0 flex-1 flex-col";
  return (
    <div className={`flex min-h-0 flex-1 flex-col ${inChat ? "lg:flex-row" : ""}`}>
      {inChat && orbOpen ? (
        <div className={`flex min-h-0 flex-col ${stage}`}>
          {result}
          <OrbStage mode="chat" onCollapse={collapse} className={`min-h-0 flex-1 ${result ? "hidden" : ""}`} />
          <div className="hidden lg:block">
            <NewChatComposer placeholder={`Start a new chat with ${assistant}`} />
          </div>
        </div>
      ) : (
        <OrbStage mode={inChat ? "chat" : "home"} collapsed={inChat} onCollapse={collapse} className={stage} />
      )}
      <div className={panel}>
        {inChat && (
          <div className="flex shrink-0 items-center gap-2 border-b border-edge px-2 py-1.5">
            <button
              onClick={closeMain}
              className="rounded-md p-1 text-slate-400 hover:bg-edge/60 hover:text-white"
              aria-label="Close chat"
              title="Close chat"
            >
              <X size={16} />
            </button>
            <span className="min-w-0 flex-1 truncate text-sm text-slate-400">{title || "Chat"}</span>
            <button
              onClick={forkOpen}
              disabled={forking}
              className="flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-xs text-slate-400 hover:bg-edge/60 hover:text-white disabled:opacity-50"
              aria-label="Fork this chat"
              title={
                wide
                  ? "Fork: copy this conversation into a second chat beside it, to try another direction"
                  : "Fork: copy this conversation into a new chat, to try another direction"
              }
            >
              <GitBranch size={14} /> <span className="hidden sm:inline">Fork</span>
            </button>
          </div>
        )}
        <Outlet />
      </div>
      {second && <SideChat id={second} mainId={chatId!} className={panel} />}
    </div>
  );
}

/** The second chat beside the main one, with its own title bar. */
function SideChat({ id, mainId, className }: { id: string; mainId: string; className: string }) {
  const navigate = useNavigate();
  const title = useStore((s) => s.chats.find((c) => c.id === id)?.title);
  const openSideChat = useStore((s) => s.openSideChat);
  const btn = "rounded-md p-1 text-slate-400 hover:bg-edge/60 hover:text-white";
  return (
    <section className={className} aria-label="Second chat">
      <div className="flex shrink-0 items-center gap-1 border-b border-edge px-2 py-1.5">
        <button onClick={() => openSideChat(null)} className={btn} aria-label="Close second chat" title="Close this chat">
          <X size={16} />
        </button>
        <GitBranch size={13} className="shrink-0 text-sky-300" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-sm text-slate-400">{title || "Chat"}</span>
        <button
          onClick={() => {
            openSideChat(mainId);
            navigate(`/c/${id}`);
          }}
          className={btn}
          aria-label="Swap the two chats"
          title="Swap: make this the main chat"
        >
          <ArrowLeftRight size={15} />
        </button>
        <button
          onClick={() => {
            const win = window.open(`/c/${id}`, `nexus-chat-${id}`, "popup,width=560,height=820");
            if (win) openSideChat(null);
          }}
          className={btn}
          aria-label="Open in its own window"
          title="Open in a window of its own"
        >
          <ExternalLink size={15} />
        </button>
      </div>
      <ChatView key={id} chatId={id} side />
    </section>
  );
}
