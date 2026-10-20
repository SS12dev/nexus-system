import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { Orb } from "../components/Orb";
import { api } from "../lib/api";
import { APP_NAME } from "../lib/assistant";

const ERRORS: Record<string, string> = {
  not_invited: "That account hasn't been invited yet. Ask an admin for an invite.",
  disabled: "This account has been disabled.",
  identity_mismatch: "That email is linked to a different sign-in identity.",
  email_unverified: "Your identity provider didn't confirm that email address.",
  oidc_failed: "Sign-in didn't complete. Try again.",
};

export function LoginPage() {
  const [params] = useSearchParams();
  const [config, setConfig] = useState<{ oidc_enabled: boolean; dev_login_enabled: boolean }>();
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState(ERRORS[params.get("error") ?? ""] ?? "");

  useEffect(() => {
    api.authConfig().then(setConfig).catch(() => setError(`${APP_NAME}'s API is not reachable.`));
  }, []);

  const devLogin = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api.devLogin(email, token);
      location.href = "/";
    } catch (err) {
      setError(ERRORS[(err as Error).message] ?? (err as Error).message);
    }
  };

  return (
    <div className="grid h-full place-items-center px-6">
      <div className="w-full max-w-sm text-center">
        <div className="mb-6 flex justify-center">
          <Orb state="idle" size={96} />
        </div>
        <h1 className="mb-1 text-2xl font-semibold">{APP_NAME}</h1>
        <p className="mb-8 text-sm text-slate-400">Sign in to talk to your assistant.</p>
        {error && <p className="mb-4 rounded-md bg-rose-950/60 px-3 py-2 text-sm text-rose-300">{error}</p>}
        {config?.oidc_enabled && (
          <a
            href="/auth/login"
            className="block rounded-lg bg-sky-500 px-4 py-2.5 font-medium text-ink hover:bg-sky-400"
          >
            Continue with Google
          </a>
        )}
        {config?.dev_login_enabled && (
          <form onSubmit={devLogin} className="mt-6 space-y-2 text-left">
            <p className="text-xs uppercase tracking-wide text-slate-500">Development sign-in</p>
            <input
              type="email"
              required
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-md border border-edge bg-panel px-3 py-2"
            />
            <input
              type="password"
              required
              placeholder="Dev sign-in token"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              className="w-full rounded-md border border-edge bg-panel px-3 py-2"
            />
            <button className="w-full rounded-md border border-edge px-3 py-2 hover:bg-edge/60">
              Sign in
            </button>
          </form>
        )}
        {config && !config.oidc_enabled && !config.dev_login_enabled && (
          <p className="text-sm text-slate-400">
            No sign-in method is configured. Set the OIDC_* variables in .env.
          </p>
        )}
      </div>
    </div>
  );
}
