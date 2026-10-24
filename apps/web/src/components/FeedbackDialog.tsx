import { X } from "lucide-react";
import { useState } from "react";
import { useLocation } from "react-router";
import { api } from "../lib/api";
import type { FeedbackKind } from "../lib/types";
import { toast } from "./Feedback";

const KINDS: { id: FeedbackKind; label: string; hint: string }[] = [
  { id: "problem", label: "Something's wrong", hint: "What happened, and what did you expect?" },
  { id: "idea", label: "An idea", hint: "What would make Nexus more useful to you?" },
  { id: "praise", label: "Something I like", hint: "What's working well?" },
];

/** Send feedback, from the menu: straight to the owner (Admin › Feedback, and by email). */
export function FeedbackDialog({ onClose }: { onClose: () => void }) {
  const { pathname } = useLocation();
  const [kind, setKind] = useState<FeedbackKind>("idea");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);

  const send = async () => {
    setSending(true);
    setError("");
    try {
      await api.sendFeedback({ kind, message: message.trim(), page: pathname });
      toast("Thanks! The owner will see it.");
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="feedback-title"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-xl border border-edge bg-panel p-5 shadow-2xl"
      >
        <div className="mb-3 flex items-center">
          <h2 id="feedback-title" className="font-medium">
            Send feedback
          </h2>
          <button onClick={onClose} aria-label="Close" className="ml-auto text-slate-400 hover:text-slate-200">
            <X size={18} />
          </button>
        </div>
        <div className="mb-3 flex flex-wrap gap-2">
          {KINDS.map((k) => (
            <button
              key={k.id}
              type="button"
              aria-pressed={kind === k.id}
              onClick={() => setKind(k.id)}
              className={`rounded-full border px-3 py-1 text-sm ${
                kind === k.id ? "border-sky-400 bg-sky-500/15 text-sky-200" : "border-edge text-slate-300 hover:bg-edge/60"
              }`}
            >
              {k.label}
            </button>
          ))}
        </div>
        <textarea
          autoFocus
          aria-label="Your feedback"
          placeholder={KINDS.find((k) => k.id === kind)!.hint}
          value={message}
          rows={5}
          maxLength={4000}
          onChange={(e) => setMessage(e.target.value)}
          className="w-full rounded-md border border-edge bg-ink px-3 py-2 text-sm"
        />
        <p className="mt-1 text-xs text-slate-500">
          Goes to the owner with your name and the page you're on. Never your chats.
        </p>
        {error && <p className="mt-2 text-sm text-rose-400">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-md border border-edge px-4 py-1.5 text-sm hover:bg-edge/60">
            Cancel
          </button>
          <button
            onClick={send}
            disabled={sending || message.trim().length < 3}
            className="rounded-md bg-sky-500 px-4 py-1.5 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
