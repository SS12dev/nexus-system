import { useEffect, useState, type ReactNode } from "react";
import { Outlet, useParams } from "react-router";
import { FilePreview } from "../components/FilePreview";
import { useAutoOpenResult } from "../components/Notices";
import { OrbStage } from "../components/OrbStage";
import { useStore } from "../store/chat";
import { useAssistantName } from "../lib/assistant";
import { NewChatComposer } from "./HomePage";

const ORB_KEY = "nexus.chat.orb";
const WIDE = "(min-width: 1024px)";

/** Whether the screen is laptop-wide (Tailwind's lg), following resizes. */
function useWide(): boolean {
  const [wide, setWide] = useState(() => typeof matchMedia === "function" && matchMedia(WIDE).matches);
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const mq = matchMedia(WIDE);
    const change = () => setWide(mq.matches);
    mq.addEventListener("change", change);
    return () => mq.removeEventListener("change", change);
  }, []);
  return wide;
}

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
 * from a notice. Closing it brings the orb back. */
export function OrbLayout() {
  const { chatId } = useParams();
  const inChat = !!chatId;
  const [orbOpen, setOrbOpen] = useState(savedOrbOpen);
  const assistant = useAssistantName();
  const wide = useWide();
  const viewer = useStore((s) => s.viewer);
  const closeViewer = useStore((s) => s.closeViewer);
  useAutoOpenResult(chatId);

  // A result belongs to its chat: moving to another chat (or home) puts it away.
  const showing = viewer && viewer.chatId === (chatId ?? null) ? viewer : null;
  useEffect(() => {
    if (viewer && viewer.chatId !== (chatId ?? null)) closeViewer();
  }, [chatId, viewer, closeViewer]);

  // The orb stays mounted under a result, so closing it finds the scene as it was.
  return (
    <>
      <OrbLayoutBody
        inChat={inChat}
        orbOpen={orbOpen || (!!showing && wide)}
        setOrbOpen={setOrbOpen}
        assistant={assistant}
        result={showing && wide ? <FilePreview inline file={showing.file} onClose={closeViewer} /> : null}
      />
      {showing && !wide && <FilePreview file={showing.file} onClose={closeViewer} />}
    </>
  );
}

function OrbLayoutBody({
  inChat,
  orbOpen,
  setOrbOpen,
  assistant,
  result = null,
}: {
  inChat: boolean;
  orbOpen: boolean;
  setOrbOpen: (open: boolean) => void;
  assistant: string;
  /** A result shown in the orb's place. */
  result?: ReactNode;
}) {

  const collapse = (collapsed: boolean) => {
    setOrbOpen(!collapsed);
    try {
      localStorage.setItem(ORB_KEY, collapsed ? "hidden" : "shown");
    } catch {
      /* private mode */
    }
  };

  const stage = !inChat ? "flex-1" : orbOpen ? "h-[34vh] shrink-0 lg:h-auto lg:flex-1" : "";
  const panel = !inChat
    ? "shrink-0"
    : orbOpen
      ? "flex min-h-0 flex-1 flex-col border-t border-edge lg:w-[440px] lg:flex-none lg:border-l lg:border-t-0 xl:w-[520px]"
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
        <Outlet />
      </div>
    </div>
  );
}
