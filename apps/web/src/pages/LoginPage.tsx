import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { Brain, Lock, Network, ShieldCheck, Users } from "lucide-react";
import { api } from "../lib/api";
import { APP_NAME } from "../lib/assistant";

const ERRORS: Record<string, string> = {
  not_invited: "That Google account hasn't been invited yet. Ask the person who runs this Nexus for an invite, then sign in with the same address.",
  disabled: "This account has been disabled.",
  identity_mismatch: "That email is linked to a different sign-in identity.",
  email_unverified: "Your identity provider didn't confirm that email address.",
  oidc_failed: "Sign-in didn't complete. Try again.",
};

const FEATURES = [
  {
    icon: Network,
    title: "Agent teams on demand",
    body: "Your assistant builds a team of specialist agents for each request, picks the right model for each one, and removes the team when the work is done.",
  },
  {
    icon: Brain,
    title: "Remembers what matters",
    body: "It learns your preferences and the people you mention, and keeps your files and results in one Library. You can review and edit everything it remembers.",
  },
  {
    icon: ShieldCheck,
    title: "Private to you",
    body: "Each person's chats, memories and files are visible only to them. Google services are used only when you ask for something that needs them.",
  },
];

export function LoginPage() {
  const [params] = useSearchParams();
  const [config, setConfig] = useState<{ oidc_enabled: boolean; dev_login_enabled: boolean }>();
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState(ERRORS[params.get("error") ?? ""] ?? "");

  useEffect(() => {
    api.authConfig().then(setConfig).catch(() => setError(`${APP_NAME}'s API is not reachable. Try again in a moment.`));
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
    <div className="login-bg relative min-h-full overflow-x-hidden">
      <div className="mx-auto flex min-h-full max-w-6xl flex-col px-5 sm:px-8">
        <header className="flex items-center gap-2.5 py-5">
          <img src="/icon.svg" alt="" className="h-8 w-8" />
          <span className="text-lg font-semibold tracking-tight">{APP_NAME}</span>
          <span className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-edge bg-panel/60 px-3 py-1 text-xs text-slate-300">
            <Lock className="h-3.5 w-3.5" aria-hidden /> Invite only
          </span>
        </header>

        <main className="grid flex-1 items-center gap-10 py-6 lg:grid-cols-[1.15fr_1fr] lg:gap-16 lg:py-4">
          <section className="order-1">
            <NetworkMark />
            <h1 className="mt-6 text-3xl font-semibold leading-tight tracking-tight sm:text-4xl lg:text-5xl">
              One assistant.
              <br />
              <span className="bg-gradient-to-r from-sky-300 via-sky-400 to-indigo-400 bg-clip-text text-transparent">
                A whole team behind it.
              </span>
            </h1>
            <p className="mt-4 max-w-xl text-base leading-relaxed text-slate-300 sm:text-lg">
              {APP_NAME} gives you a personal AI assistant that you name yourself. Ask it anything; for
              real work it assembles agent teams to research, plan, write and build, and shows you
              what they're doing as they go.
            </p>
            <ul className="mt-8 hidden gap-5 sm:grid lg:max-w-xl">
              {FEATURES.map((f) => (
                <Feature key={f.title} {...f} />
              ))}
            </ul>
          </section>

          <section className="order-2 w-full lg:justify-self-end">
            <div className="w-full rounded-2xl border border-edge bg-panel/80 p-6 shadow-2xl shadow-sky-950/40 backdrop-blur sm:p-8 lg:w-[400px]">
              <h2 className="text-xl font-semibold">Sign in</h2>
              <p className="mt-1.5 text-sm leading-relaxed text-slate-400">
                Use the Google account your invite was sent to.
              </p>

              {error && (
                <p role="alert" className="mt-5 rounded-lg border border-rose-900/60 bg-rose-950/50 px-3 py-2.5 text-sm text-rose-200">
                  {error}
                </p>
              )}

              <div className="mt-6">
                {!config && !error && <div className="h-10 animate-pulse rounded-full bg-edge/60" aria-label="Loading sign-in options" />}
                {config?.oidc_enabled && <GoogleButton />}
                {config?.dev_login_enabled && (
                  <form onSubmit={devLogin} className={`space-y-2 ${config.oidc_enabled ? "mt-6 border-t border-edge pt-5" : ""}`}>
                    <p className="text-xs uppercase tracking-wide text-slate-500">Development sign-in</p>
                    <input
                      type="email"
                      required
                      placeholder="you@example.com"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      className="w-full rounded-md border border-edge bg-ink px-3 py-2"
                    />
                    <input
                      type="password"
                      required
                      placeholder="Dev sign-in token"
                      value={token}
                      onChange={(e) => setToken(e.target.value)}
                      className="w-full rounded-md border border-edge bg-ink px-3 py-2"
                    />
                    <button className="w-full rounded-md border border-edge px-3 py-2 hover:bg-edge/60">Sign in</button>
                  </form>
                )}
                {config && !config.oidc_enabled && !config.dev_login_enabled && (
                  <p className="text-sm text-slate-400">
                    No sign-in method is configured. Set the OIDC_* variables in .env.
                  </p>
                )}
              </div>

              <div className="mt-6 space-y-3 border-t border-edge pt-5 text-sm leading-relaxed text-slate-400">
                <p className="flex gap-2.5">
                  <Users className="mt-0.5 h-4 w-4 shrink-0 text-sky-400" aria-hidden />
                  <span>
                    <span className="text-slate-200">{APP_NAME} is invite-only.</span> Only Google accounts
                    that have been invited can sign in. No invite yet? Ask the person who runs it.
                  </span>
                </p>
                <p className="flex gap-2.5">
                  <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-sky-400" aria-hidden />
                  <span>
                    Google will also ask whether your assistant may use Gmail, Calendar, Contacts and
                    Tasks. Each is optional, and you can disconnect any of them later on the Connections page.
                  </span>
                </p>
              </div>
            </div>
          </section>

          <ul className="order-3 grid gap-5 sm:hidden">
            {FEATURES.map((f) => (
              <Feature key={f.title} {...f} />
            ))}
          </ul>
        </main>

        <footer className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-edge/60 py-5 text-xs text-slate-500">
          <span>ServHub {APP_NAME}, a private assistant for invited people</span>
          <a href="/privacy" className="hover:text-slate-300">
            Privacy
          </a>
        </footer>
      </div>
    </div>
  );
}

function Feature({ icon: Icon, title, body }: (typeof FEATURES)[number]) {
  return (
    <li className="flex gap-4">
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-edge bg-panel text-sky-400">
        <Icon className="h-5 w-5" aria-hidden />
      </span>
      <div>
        <h3 className="font-medium text-slate-100">{title}</h3>
        <p className="mt-0.5 text-sm leading-relaxed text-slate-400">{body}</p>
      </div>
    </li>
  );
}

/** Google's standard "Sign in with Google" button (light theme, pill shape), per Google's
 * branding guidelines: white fill, #747775 outline, the four-colour G and #1F1F1F label. */
function GoogleButton() {
  return (
    <a
      href="/auth/login"
      className="flex h-10 w-full items-center justify-center gap-2.5 rounded-full border border-[#747775] bg-white px-3 text-sm font-medium text-[#1f1f1f] transition-colors hover:bg-[#f2f2f2] active:bg-[#e8e8e8]"
      style={{ fontFamily: "Roboto, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif" }}
    >
      <svg viewBox="0 0 48 48" className="h-[18px] w-[18px]" aria-hidden>
        <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
        <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
        <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
        <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
      </svg>
      Sign in with Google
    </a>
  );
}

/** A light, CSS-animated picture of the idea: the assistant at the centre with agent teams
 * orbiting and linking to it. Plain SVG, so the login page needs no WebGL. */
function NetworkMark() {
  const nodes = [
    [70, 14], [118, 40], [122, 96], [76, 126], [24, 104], [16, 46],
  ];
  return (
    <svg viewBox="0 0 140 140" className="h-24 w-24 sm:h-28 sm:w-28" aria-hidden>
      <defs>
        <radialGradient id="login-core" cx="50%" cy="42%" r="55%">
          <stop offset="0" stopColor="#e0f2fe" />
          <stop offset="0.4" stopColor="#38bdf8" />
          <stop offset="1" stopColor="#1e3a8a" />
        </radialGradient>
      </defs>
      <circle cx="70" cy="70" r="58" fill="none" stroke="#38bdf8" strokeOpacity="0.18" strokeDasharray="2 5" />
      <g className="login-orbit">
        {nodes.map(([x, y], i) => (
          <line key={`l${i}`} x1="70" y1="70" x2={x} y2={y} stroke="#38bdf8" strokeOpacity="0.35" strokeWidth="1" />
        ))}
        {nodes.map(([x, y], i) => (
          <line
            key={`r${i}`}
            x1={x}
            y1={y}
            x2={nodes[(i + 1) % nodes.length][0]}
            y2={nodes[(i + 1) % nodes.length][1]}
            stroke="#818cf8"
            strokeOpacity="0.25"
            strokeWidth="1"
          />
        ))}
        {nodes.map(([x, y], i) => (
          <circle key={`n${i}`} cx={x} cy={y} r={i % 2 ? 4 : 5} fill={i % 2 ? "#818cf8" : "#7dd3fc"} className="login-node" style={{ animationDelay: `${i * 0.4}s` }} />
        ))}
      </g>
      <circle cx="70" cy="70" r="24" fill="url(#login-core)" className="login-core" />
    </svg>
  );
}
