import { Check, Copy, Eye, EyeOff, KeyRound, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import type { SecretDetails } from "../../lib/money";
import { toast } from "../Feedback";

const SHOWN_FOR_MS = 30_000;

/** An account number and nominee on a savings or loan tile: masked, with an eye button that
 * fetches the real values (each look is kept in the audit log) and hides them again after 30
 * seconds. With nothing saved, a quiet link to add them. */
export function SecretLine({
  label,
  masked,
  branch,
  nominee,
  reveal,
  onAdd,
}: {
  label: string;
  masked: string | null;
  branch?: string | null;
  /** The relation, or "set", when a nominee is saved. */
  nominee?: string | null;
  reveal: () => Promise<SecretDetails>;
  onAdd: () => void;
}) {
  const [shown, setShown] = useState<SecretDetails | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!shown) return;
    const t = setTimeout(() => setShown(null), SHOWN_FOR_MS);
    return () => clearTimeout(t);
  }, [shown]);

  if (!masked && !nominee && !branch)
    return (
      <button onClick={onAdd} className="mt-2 flex items-center gap-1 text-xs text-slate-500 hover:text-sky-300">
        <KeyRound size={11} /> Add the {label.toLowerCase()} and nominee
      </button>
    );

  const toggle = async () => {
    if (shown) return setShown(null);
    setBusy(true);
    try {
      setShown(await reveal());
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const copy = async () => {
    if (!shown?.account_no) return;
    await navigator.clipboard.writeText(shown.account_no);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  const who = nominee ? (shown?.nominee ? `${shown.nominee}${nominee !== "set" ? ` (${nominee})` : ""}` : nominee === "set" ? "set" : `your ${nominee.toLowerCase()}`) : null;
  return (
    <div className="mt-2 flex items-start gap-2 rounded-lg bg-ink/60 px-2.5 py-1.5 text-xs text-slate-400">
      <span className="min-w-0 flex-1 space-y-0.5">
        {masked && (
          <span className="block">
            <span className="text-slate-500">{label}: </span>
            <span className="font-mono tracking-wide text-slate-200 tabular-nums">{shown?.account_no ?? masked}</span>
            {shown?.account_no && (
              <button onClick={copy} className="ml-1.5 inline-flex align-middle text-slate-500 hover:text-slate-200" aria-label="Copy the number">
                {copied ? <Check size={11} /> : <Copy size={11} />}
              </button>
            )}
          </span>
        )}
        {(branch || who) && (
          <span className="block truncate">
            {branch}
            {branch && who && " · "}
            {who && (
              <>
                Nominee: <span className="text-slate-200">{who}</span>
              </>
            )}
          </span>
        )}
      </span>
      {(masked || nominee) && (
        <button
          onClick={toggle}
          disabled={busy}
          className="-m-1 shrink-0 rounded p-1 text-slate-400 hover:bg-edge/70 hover:text-white"
          aria-label={shown ? "Hide the number and nominee" : "Show the number and nominee"}
          aria-pressed={!!shown}
          title={shown ? "Hide" : "Show (hides again after 30 seconds)"}
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : shown ? <EyeOff size={13} /> : <Eye size={13} />}
        </button>
      )}
    </div>
  );
}
