import { useState } from "react";
import { Outlet, useParams } from "react-router";
import { OrbStage } from "../components/OrbStage";

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
 * be folded away. */
export function OrbLayout() {
  const { chatId } = useParams();
  const inChat = !!chatId;
  const [orbOpen, setOrbOpen] = useState(savedOrbOpen);

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
      <OrbStage mode={inChat ? "chat" : "home"} collapsed={inChat && !orbOpen} onCollapse={collapse} className={stage} />
      <div className={panel}>
        <Outlet />
      </div>
    </div>
  );
}
