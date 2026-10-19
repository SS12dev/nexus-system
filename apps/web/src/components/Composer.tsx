import { Paperclip, SendHorizontal } from "lucide-react";
import { useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent } from "react";
import { api } from "../lib/api";
import { ACCEPT } from "../lib/format";
import { useStore } from "../store/chat";
import { FileChip } from "./FileChip";

interface Pending {
  key: string;
  name: string;
  size: number;
  id?: string;
  error?: string;
}

export function Composer({
  onSend,
  disabled,
  placeholder,
  chatId,
}: {
  onSend: (text: string, fileIds: string[]) => Promise<boolean>;
  disabled?: boolean;
  placeholder?: string;
  chatId?: string;
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [pending, setPending] = useState<Pending[]>([]);
  const [dragging, setDragging] = useState(false);
  const files = useStore((s) => s.files);
  const upsertFile = useStore((s) => s.upsertFile);
  const picker = useRef<HTMLInputElement>(null);

  const statusOf = (p: Pending) =>
    p.error ? "failed" : p.id ? (files[p.id]?.status ?? "processing") : "uploading";
  const busyFiles = pending.some((p) => ["uploading", "processing"].includes(statusOf(p)));
  const ready = pending.filter((p) => statusOf(p) === "ready");

  const addFiles = (list: FileList | File[]) => {
    for (const file of Array.from(list)) {
      const key = `${file.name}-${file.size}-${Math.random()}`;
      setPending((ps) => [...ps, { key, name: file.name, size: file.size }]);
      api
        .uploadFile(file, chatId)
        .then((f) => {
          upsertFile(f);
          setPending((ps) => ps.map((p) => (p.key === key ? { ...p, id: f.id } : p)));
        })
        .catch((e: Error) =>
          setPending((ps) => ps.map((p) => (p.key === key ? { ...p, error: e.message } : p))),
        );
    }
  };

  const submit = async () => {
    const content = text.trim();
    if (!content || disabled || sending || busyFiles) return;
    setSending(true);
    if (await onSend(content, ready.map((p) => p.id!))) {
      setText("");
      setPending([]);
    }
    setSending(false);
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };
  const onPaste = (e: ClipboardEvent) => {
    if (e.clipboardData.files.length) {
      e.preventDefault();
      addFiles(e.clipboardData.files);
    }
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (!disabled && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  };

  return (
    <div
      className="border-t border-edge bg-ink/80 px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3 backdrop-blur"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <div
        className={`mx-auto max-w-3xl rounded-2xl border bg-panel px-3 py-2 ${
          dragging ? "border-sky-400" : "border-edge"
        }`}
      >
        {pending.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {pending.map((p) => (
              <FileChip
                key={p.key}
                file={p}
                status={statusOf(p)}
                error={p.error ?? (p.id ? files[p.id]?.error : null)}
                onRemove={() => setPending((ps) => ps.filter((x) => x.key !== p.key))}
              />
            ))}
          </div>
        )}
        <div className="flex items-end gap-2">
          <button
            onClick={() => picker.current?.click()}
            disabled={disabled}
            className="p-1.5 text-slate-400 hover:text-white disabled:opacity-30"
            aria-label="Attach files"
            title="Attach files"
          >
            <Paperclip size={18} />
          </button>
          <input
            ref={picker}
            type="file"
            multiple
            accept={ACCEPT}
            hidden
            onChange={(e) => {
              if (e.target.files) addFiles(e.target.files);
              e.target.value = "";
            }}
          />
          {/* Phase 6 adds a push-to-talk button here. */}
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKey}
            onPaste={onPaste}
            rows={1}
            disabled={disabled}
            placeholder={
              placeholder ?? (pending.length ? "Ask about the attached files" : "Message Jarvis")
            }
            className="max-h-48 min-h-[1.5rem] flex-1 resize-none bg-transparent py-1 outline-none [field-sizing:content] placeholder:text-slate-500 disabled:opacity-50"
          />
          <button
            onClick={submit}
            disabled={disabled || sending || busyFiles || !text.trim()}
            className="rounded-full bg-sky-500 p-2 text-ink disabled:opacity-30"
            aria-label="Send"
            title={busyFiles ? "Waiting for Jarvis to finish reading your files" : "Send"}
          >
            <SendHorizontal size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
