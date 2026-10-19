import { SendHorizontal } from "lucide-react";
import { useState, type KeyboardEvent } from "react";

export function Composer({
  onSend,
  disabled,
  placeholder,
}: {
  onSend: (text: string) => Promise<boolean>;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);

  const submit = async () => {
    const content = text.trim();
    if (!content || disabled || sending) return;
    setSending(true);
    if (await onSend(content)) setText("");
    setSending(false);
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="border-t border-edge bg-ink/80 px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3 backdrop-blur">
      <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-edge bg-panel px-3 py-2">
        {/* Phase 6 adds a push-to-talk button here. */}
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          rows={1}
          disabled={disabled}
          placeholder={placeholder ?? "Message Jarvis"}
          className="max-h-48 min-h-[1.5rem] flex-1 resize-none bg-transparent py-1 outline-none [field-sizing:content] placeholder:text-slate-500 disabled:opacity-50"
        />
        <button
          onClick={submit}
          disabled={disabled || sending || !text.trim()}
          className="rounded-full bg-sky-500 p-2 text-ink disabled:opacity-30"
          aria-label="Send"
        >
          <SendHorizontal size={16} />
        </button>
      </div>
    </div>
  );
}
