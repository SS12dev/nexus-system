import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";

export interface TextPromptField {
  key: string;
  label: string;
  value: string;
  placeholder?: string;
  maxLength?: number;
  multiline?: boolean;
  optional?: boolean;
}

interface TextPromptDialogProps {
  title: string;
  description?: string;
  fields: TextPromptField[];
  confirmLabel: string;
  danger?: boolean;
  onSubmit: (values: Record<string, string>) => Promise<void>;
  onClose: () => void;
}

export function TextPromptDialog({
  title,
  description,
  fields,
  confirmLabel,
  danger = false,
  onSubmit,
  onClose,
}: TextPromptDialogProps) {
  const [values, setValues] = useState(() =>
    Object.fromEntries(fields.map((field) => [field.key, field.value])),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDivElement>(null);
  const firstField = useRef<HTMLInputElement | HTMLTextAreaElement>(null);

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    firstField.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        event.preventDefault();
        onClose();
      }
      if (event.key !== "Tab") return;
      const focusable = dialog.current?.querySelectorAll<HTMLElement>(
        'input:not(:disabled), textarea:not(:disabled), button:not(:disabled)',
      );
      if (!focusable?.length) return;
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      before?.focus();
    };
  }, [busy, onClose]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await onSubmit(values);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  const invalid = fields.some(
    (field) => !field.optional && !values[field.key]?.trim(),
  );

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="text-prompt-title"
        aria-describedby={description ? "text-prompt-description" : undefined}
        className="w-full max-w-md rounded-xl border border-edge bg-panel p-5 shadow-2xl shadow-black/50"
      >
        <h2 id="text-prompt-title" className="font-semibold text-slate-100">
          {title}
        </h2>
        {description && (
          <p id="text-prompt-description" className="mt-1.5 text-sm leading-relaxed text-slate-400">
            {description}
          </p>
        )}
        <form onSubmit={submit} className="mt-4 space-y-3">
          {fields.map((field, index) => {
            const common = {
              id: `text-prompt-${field.key}`,
              value: values[field.key] ?? "",
              maxLength: field.maxLength,
              placeholder: field.placeholder,
              disabled: busy,
              onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
                setValues((current) => ({ ...current, [field.key]: event.target.value })),
              className:
                "w-full rounded-md border border-edge bg-ink px-3 py-2 text-base text-slate-100 placeholder:text-slate-600 focus:border-sky-500 focus:outline-none sm:text-sm",
            };
            return (
              <label key={field.key} htmlFor={common.id} className="block text-sm text-slate-300">
                <span className="mb-1 block">{field.label}</span>
                {field.multiline ? (
                  <textarea
                    {...common}
                    ref={(node) => {
                      if (index === 0) firstField.current = node;
                    }}
                    rows={3}
                  />
                ) : (
                  <input
                    {...common}
                    ref={(node) => {
                      if (index === 0) firstField.current = node;
                    }}
                  />
                )}
              </label>
            );
          })}
          {error && <p role="alert" className="text-sm text-rose-300">{error}</p>}
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              disabled={busy}
              onClick={onClose}
              className="rounded-md border border-edge px-3.5 py-1.5 text-sm hover:bg-edge/60 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={busy || invalid}
              className={`rounded-md px-3.5 py-1.5 text-sm font-medium disabled:opacity-50 ${
                danger
                  ? "bg-rose-600 text-white hover:bg-rose-500"
                  : "bg-sky-500 text-ink hover:bg-sky-400"
              }`}
            >
              {busy ? "Saving…" : confirmLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
