import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { api } from "../lib/api";
import { useAssistantName } from "../lib/assistant";
import { useStore } from "../store/chat";

type Step = { anchor: string; title: string; body: (assistant: string) => string };

/** The first-run tour: a few stops after setup, once per person (profile.tour_done). Each stop
 * highlights an element marked data-tour="…"; on a phone, where the menu is folded away, menu
 * stops point at the menu button instead. */
const STEPS: Step[] = [
  {
    anchor: "composer",
    title: "Ask anything",
    body: (a) =>
      `Type here, or tap one of the suggestions. Attach files with the clip. For bigger jobs ${a} puts a small team of agents on it and shows them flying out from the hub in your universe.`,
  },
  {
    anchor: "routines",
    title: "Routines",
    body: (a) =>
      `Things ${a} does for you on a schedule, like a morning brief. Results show there, not in your chats, and can notify your phone.`,
  },
  {
    anchor: "memory",
    title: "Your mind",
    body: (a) =>
      `Everything ${a} knows about you is here: who you are, the areas of your life and the memories underneath. Correct or forget anything.`,
  },
  {
    anchor: "settings",
    title: "Settings",
    body: () => "Turn on notifications, pick light or dark, connect Google, or download your data.",
  },
  {
    anchor: "feedback",
    title: "Tell us what you think",
    body: () => "Something broken, or an idea? Send it from here, straight to the owner.",
  },
];

type Rect = { top: number; left: number; width: number; height: number };
const PAD = 6;
const CARD_W = 300;

function visibleRect(selector: string): Rect | null {
  for (const el of document.querySelectorAll<HTMLElement>(selector)) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && r.right > 0 && r.left < window.innerWidth) {
      return { top: r.top, left: r.left, width: r.width, height: r.height };
    }
  }
  return null;
}

export function Tour() {
  const user = useStore((s) => s.user)!;
  const setUser = useStore((s) => s.setUser);
  const assistant = useAssistantName();
  const [i, setI] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  const [closed, setClosed] = useState(false);
  const active = !!user.onboarded_at && !user.profile?.tour_done && !closed;

  const measure = useCallback(() => {
    const step = STEPS[i];
    setRect(visibleRect(`[data-tour="${step.anchor}"]`) ?? visibleRect('[data-tour="menu"]'));
  }, [i]);

  useLayoutEffect(() => {
    if (active) measure();
  }, [active, measure]);
  useEffect(() => {
    if (!active) return;
    // The orb and pages settle after first paint; measure again shortly, and on resize.
    const t = window.setTimeout(measure, 400);
    window.addEventListener("resize", measure);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("resize", measure);
    };
  }, [active, measure]);

  const finish = async () => {
    setClosed(true);
    try {
      setUser(await api.updateMe({ tour_done: true }));
    } catch {
      /* it shows again next visit, which is fine */
    }
  };

  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") finish();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  if (!active) return null;
  const step = STEPS[i];
  const last = i === STEPS.length - 1;

  // The card sits below the highlight when there's room, otherwise above it.
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const width = Math.min(CARD_W, vw - 24);
  let cardStyle: React.CSSProperties = { width, left: (vw - width) / 2, top: vh / 2 - 80 };
  if (rect) {
    const below = rect.top + rect.height + PAD + 12;
    const left = Math.min(Math.max(12, rect.left), vw - width - 12);
    cardStyle =
      below + 180 < vh
        ? { width, left, top: below }
        : { width, left, bottom: vh - rect.top + PAD + 12 };
  }

  return (
    <div className="fixed inset-0 z-[60]" aria-live="polite">
      {rect ? (
        <div
          className="pointer-events-none fixed rounded-xl ring-2 ring-sky-400 transition-all duration-300"
          style={{
            top: rect.top - PAD,
            left: rect.left - PAD,
            width: rect.width + PAD * 2,
            height: rect.height + PAD * 2,
            boxShadow: "0 0 0 9999px rgb(2 6 23 / 0.6)",
          }}
        />
      ) : (
        <div className="fixed inset-0 bg-slate-950/60" />
      )}
      <div
        role="dialog"
        aria-label={step.title}
        className="fixed rounded-xl border border-edge bg-panel p-4 shadow-2xl"
        style={cardStyle}
      >
        <div className="mb-1 text-xs text-slate-500">
          {i + 1} of {STEPS.length}
        </div>
        <h2 className="mb-1 font-medium">{step.title}</h2>
        <p className="text-sm text-slate-300">{step.body(assistant)}</p>
        <div className="mt-4 flex items-center gap-2">
          <button onClick={finish} className="text-sm text-slate-500 hover:text-slate-300">
            Skip tour
          </button>
          {i > 0 && (
            <button
              onClick={() => setI(i - 1)}
              className="ml-auto rounded-md border border-edge px-3 py-1 text-sm hover:bg-edge/60"
            >
              Back
            </button>
          )}
          <button
            autoFocus
            onClick={() => (last ? finish() : setI(i + 1))}
            className={`rounded-md bg-sky-500 px-3 py-1 text-sm font-medium text-ink hover:bg-sky-400 ${i === 0 ? "ml-auto" : ""}`}
          >
            {last ? "Done" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}
