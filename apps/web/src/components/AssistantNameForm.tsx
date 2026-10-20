import { useState, type FormEvent } from "react";
import {
  ASSISTANT_NAME_MAX,
  cleanAssistantName,
  DEFAULT_ASSISTANT_NAME,
  SUGGESTED_NAMES,
} from "../lib/assistant";
import { api } from "../lib/api";
import { useStore } from "../store/chat";

/** Pick or type a name for your assistant and save it. Used on the first-run screen and in
 * Settings. */
export function AssistantNameForm({ submitLabel = "Save" }: { submitLabel?: string }) {
  const current = useStore((s) => s.user?.assistant_name);
  const setUser = useStore((s) => s.setUser);
  const [value, setValue] = useState(current ?? DEFAULT_ASSISTANT_NAME);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const name = cleanAssistantName(value);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name) return;
    setSaving(true);
    setError("");
    try {
      setUser(await api.nameAssistant(name));
      setSaved(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {SUGGESTED_NAMES.map((n) => (
          <button
            key={n}
            type="button"
            onClick={() => {
              setValue(n);
              setSaved(false);
            }}
            className={`rounded-full border px-3 py-1 text-sm ${
              value === n ? "border-sky-400 text-sky-300" : "border-edge hover:bg-edge/60"
            }`}
          >
            {n}
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          aria-label="Assistant name"
          value={value}
          maxLength={ASSISTANT_NAME_MAX}
          onChange={(e) => {
            setValue(e.target.value);
            setSaved(false);
          }}
          className="min-w-0 flex-1 rounded-md border border-edge bg-panel px-3 py-2"
        />
        <button
          disabled={!name || saving || (name === current && !!current)}
          className="rounded-md bg-sky-500 px-4 py-2 font-medium text-ink hover:bg-sky-400 disabled:opacity-50"
        >
          {submitLabel}
        </button>
      </div>
      {value.trim() && !name && (
        <p className="text-xs text-rose-400">
          Use up to {ASSISTANT_NAME_MAX} letters, numbers, spaces, dots, hyphens or apostrophes.
        </p>
      )}
      {error && <p className="text-xs text-rose-400">{error}</p>}
      {saved && current && <p className="text-xs text-emerald-400">Saved. Say hello to {current}.</p>}
    </form>
  );
}
