import { Check, Pencil, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import type { Approval } from "../lib/types";

/** A risky step an agent wants to take: approve as is, edit the arguments, or reject. */
export function ApprovalCard({ approval, systemName }: { approval: Approval; systemName?: string }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(JSON.stringify(approval.args, null, 2));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const decide = async (decision: "approve" | "reject") => {
    setError("");
    let args: Record<string, unknown> | undefined;
    if (decision === "approve" && editing) {
      try {
        args = JSON.parse(text);
      } catch {
        setError("The arguments aren't valid JSON.");
        return;
      }
    }
    setBusy(true);
    try {
      await api.decide(approval.id, decision, { args, note: note || undefined });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (approval.status !== "pending") {
    return (
      <div className="rounded-xl border border-edge bg-panel px-3 py-2 text-sm text-slate-400">
        {approval.status === "approved" ? "Approved" : "Rejected"}: {approval.agent} · {approval.tool}
        {approval.decided_args && " (edited)"}
        {approval.note && ` · "${approval.note}"`}
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-rose-500/40 bg-rose-500/5 p-3">
      <div className="mb-2 flex items-center gap-2 text-sm">
        <ShieldAlert size={16} className="text-rose-300" />
        <span className="font-medium">
          {approval.agent} wants to use <code>{approval.tool}</code>
        </span>
        {systemName && <span className="ml-auto truncate text-xs text-slate-500">{systemName}</span>}
      </div>
      {approval.reason && <p className="mb-2 text-xs text-slate-400">{approval.reason}</p>}
      {editing ? (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={Math.min(14, text.split("\n").length + 1)}
          className="w-full rounded-md border border-edge bg-ink p-2 font-mono text-xs"
          aria-label="Arguments"
        />
      ) : (
        <pre className="max-h-56 overflow-auto rounded-md border border-edge bg-ink p-2 text-xs">
          {JSON.stringify(approval.args, null, 2)}
        </pre>
      )}
      <input
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Note for the agent (optional)"
        className="mt-2 w-full rounded-md border border-edge bg-ink px-2 py-1 text-sm"
      />
      {error && <p className="mt-1 text-xs text-rose-400">{error}</p>}
      <div className="mt-2 flex flex-wrap gap-2">
        <button
          disabled={busy}
          onClick={() => decide("approve")}
          className="flex items-center gap-1 rounded-md bg-emerald-600 px-3 py-1 text-sm hover:bg-emerald-500 disabled:opacity-40"
        >
          <Check size={14} /> {editing ? "Approve edited" : "Approve"}
        </button>
        <button
          disabled={busy}
          onClick={() => setEditing((v) => !v)}
          className="flex items-center gap-1 rounded-md border border-edge px-3 py-1 text-sm hover:bg-edge/60"
        >
          <Pencil size={14} /> {editing ? "Cancel edit" : "Edit"}
        </button>
        <button
          disabled={busy}
          onClick={() => decide("reject")}
          className="flex items-center gap-1 rounded-md border border-edge px-3 py-1 text-sm hover:bg-rose-500/20"
        >
          <X size={14} /> Reject
        </button>
      </div>
    </div>
  );
}
