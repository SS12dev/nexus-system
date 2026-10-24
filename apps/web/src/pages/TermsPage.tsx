import { useState } from "react";
import { Orb } from "../components/Orb";
import { api } from "../lib/api";
import { useStore } from "../store/chat";

/** Shown once when the Terms of Service or Privacy Policy changed since the person agreed. */
export function TermsPage() {
  const user = useStore((s) => s.user)!;
  const setUser = useStore((s) => s.setUser);
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const accept = async () => {
    setSaving(true);
    setError("");
    try {
      setUser(await api.acceptTerms(user.terms_current!));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const signOut = async () => {
    await api.logout().catch(() => {});
    window.location.href = "/login";
  };

  return (
    <div className="flex h-full items-center justify-center overflow-y-auto px-4 py-10">
      <div className="w-full max-w-md text-center">
        <div className="mb-6 flex justify-center">
          <Orb state="idle" size={72} />
        </div>
        <h1 className="mb-2 text-2xl font-semibold">We've updated our terms</h1>
        <p className="mb-6 text-sm text-slate-400">
          Please read the updated{" "}
          <a href="/terms" target="_blank" rel="noopener" className="text-sky-300 underline">
            Terms of Service
          </a>{" "}
          and{" "}
          <a href="/privacy" target="_blank" rel="noopener" className="text-sky-300 underline">
            Privacy Policy
          </a>{" "}
          (version {user.terms_current}) to keep using Nexus.
        </p>
        <label className="mb-4 flex cursor-pointer items-start gap-3 rounded-lg border border-edge bg-panel p-3 text-left text-sm">
          <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} className="mt-0.5" />
          <span className="text-slate-300">I agree to the updated Terms of Service and Privacy Policy.</span>
        </label>
        {error && <p className="mb-3 text-sm text-rose-400">{error}</p>}
        <div className="flex gap-2">
          <button onClick={signOut} className="flex-1 rounded-md border border-edge px-4 py-2 text-sm hover:bg-edge/60">
            Sign out
          </button>
          <button
            onClick={accept}
            disabled={!agreed || saving}
            className="flex-1 rounded-md bg-sky-500 px-4 py-2 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-50"
          >
            Continue
          </button>
        </div>
      </div>
    </div>
  );
}
