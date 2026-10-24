import { useState } from "react";
import { Outlet, useParams } from "react-router";
import { OrbStage } from "../components/OrbStage";
import { useAssistantName } from "../lib/assistant";
import { NewChatComposer } from "./HomePage";

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
 * orb strip is too short, and two message boxes on one small screen would be confusing. */
export function OrbLayout() {
  const { chatId } = useParams();
  const inChat = !!chatId;
  const [orbOpen, setOrbOpen] = useState(savedOrbOpen);
  const assistant = useAssistantName();

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
          <OrbStage mode="chat" onCollapse={collapse} className="min-h-0 flex-1" />
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
