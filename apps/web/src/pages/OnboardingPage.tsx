import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Briefcase,
  BriefcaseBusiness,
  CalendarDays,
  Check,
  ChefHat,
  CodeXml,
  Dumbbell,
  FlaskConical,
  GraduationCap,
  House,
  Lightbulb,
  Mail,
  Megaphone,
  Newspaper,
  Palette,
  PenLine,
  Plane,
  Printer,
  Rocket,
  Scale,
  School,
  Search,
  Sparkles,
  Stethoscope,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { uploadInBackground } from "../components/ImportHistory";
import { Orb } from "../components/Orb";
import { Select } from "../components/Select";
import { api } from "../lib/api";
import {
  ASSISTANT_NAME_MAX,
  cleanAssistantName,
  DEFAULT_ASSISTANT_NAME,
  SUGGESTED_NAMES,
} from "../lib/assistant";
import { defaultPicks, packsFor, routineTime, scheduleText } from "../lib/onboarding";
import { enablePush } from "../lib/push";
import { browserTimezone, timezones } from "../lib/schedule";
import type {
  AnswerStyle,
  Channel,
  GoogleAccess,
  OnboardingAnswers,
  Profiles,
  User,
} from "../lib/types";
import { useStore } from "../store/chat";

const STEPS = ["Name", "Work", "Day", "Help", "Style", "Routines"] as const;
// Icons for the profession and interest tiles; anything new falls back to a sparkle.
const ICONS: Record<string, LucideIcon> = {
  student: GraduationCap,
  software: CodeXml,
  research: FlaskConical,
  founder: Rocket,
  creative: Palette,
  sales: Megaphone,
  healthcare: Stethoscope,
  teaching: School,
  finance: Scale,
  freelance: Briefcase,
  other: Sparkles,
  email: Mail,
  calendar: CalendarDays,
  writing: PenLine,
  learning: Lightbulb,
  reading: BookOpen,
  news: Newspaper,
  job_hunt: BriefcaseBusiness,
  coding: CodeXml,
  fitness: Dumbbell,
  cooking: ChefHat,
  money: Wallet,
  travel: Plane,
  family: House,
  making: Printer,
};
const INTEREST_ICONS: Record<string, LucideIcon> = { research: Search };
// More than this many routines and the step warns about budget.
const MANY_ROUTINES = 5;
const SERVICE_LABEL: Record<string, string> = {
  gmail: "Gmail",
  calendar: "Calendar",
  tasks: "Tasks",
  contacts: "Contacts",
};

/** The setup interview after first sign-in, and again from Settings › Re-run setup. Every
 * step can be skipped; the answers become memories (editable on the Memory page), routines
 * from the matching profile packs, and a welcome chat. */
export function OnboardingPage({ onDone }: { onDone?: () => void }) {
  const user = useStore((s) => s.user)!;
  const setUser = useStore((s) => s.setUser);
  const navigate = useNavigate();
  const first = user.name?.split(" ")[0];
  const prior = user.profile ?? {};

  const [step, setStep] = useState(0);
  const [profiles, setProfiles] = useState<Profiles | null>(null);
  const [google, setGoogle] = useState<GoogleAccess | null>(null);
  const [name, setName] = useState(user.assistant_name ?? DEFAULT_ASSISTANT_NAME);
  const askTerms = !!user.terms_needed;
  const [agreed, setAgreed] = useState(!askTerms);
  const [roles, setRoles] = useState<string[]>(prior.roles ?? []);
  const [roleNote, setRoleNote] = useState(prior.role_note ?? "");
  const [currentFocus, setCurrentFocus] = useState(prior.current_focus ?? "");
  const [tools, setTools] = useState(prior.tools ?? "");
  const [timezone, setTimezone] = useState(user.timezone || browserTimezone());
  const [dayStart, setDayStart] = useState(prior.day_start || "08:00");
  const [interests, setInterests] = useState<string[]>(prior.interests ?? []);
  const [about, setAbout] = useState(prior.about ?? "");
  const [style, setStyle] = useState<AnswerStyle>(
    prior.style ?? { length: "balanced", tone: "casual", language: "" },
  );
  // Routine key -> chosen time ("HH:MM"), for the ones ticked.
  const [picked, setPicked] = useState<Record<string, string> | null>(null);
  const [delivery, setDelivery] = useState<Channel[]>([]);
  const [phone, setPhone] = useState(user.phone ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<{ me: User; chat_id: string | null; routines: number } | null>(
    null,
  );

  useEffect(() => {
    api.profiles().then(setProfiles).catch((e) => setError(e.message));
    api.googleAccess().then(setGoogle).catch(() => setGoogle(null));
  }, []);

  const cleanName = cleanAssistantName(name);
  const packs = useMemo(
    () => (profiles ? packsFor(profiles.packs, roles, interests) : []),
    [profiles, roles, interests],
  );
  const offered = packs.flatMap((p) => p.routines.map((r) => ({ pack: p, routine: r })));
  const canMember = user.role !== "viewer";
  const channels = user.channels ?? [];

  // Tick each offered routine's default the first time the routines step is shown.
  const openRoutines = () => {
    if (picked === null) {
      const start: Record<string, string> = {};
      const keys = new Set(defaultPicks(packs));
      for (const { routine } of offered) {
        if (keys.has(routine.key)) start[routine.key] = routineTime(routine, dayStart);
      }
      setPicked(start);
    }
    setStep(5);
  };

  const finish = async (skip = false) => {
    if (!cleanName) {
      setStep(0);
      return;
    }
    setSaving(true);
    setError("");
    const terms = askTerms ? { accept_terms: user.terms_current } : {};
    const answers: Partial<OnboardingAnswers> = skip
      ? { assistant_name: cleanName, timezone, ...terms }
      : {
          ...terms,
          assistant_name: cleanName,
          roles,
          role_note: roleNote,
          current_focus: currentFocus,
          tools,
          interests,
          about,
          timezone,
          day_start: dayStart,
          style,
          routines: Object.entries(picked ?? {}).map(([key, time]) => ({ key, time })),
          delivery,
          ...(delivery.includes("whatsapp") && phone.trim() ? { phone: phone.trim() } : {}),
        };
    try {
      setDone(await api.onboard(answers));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const enter = () => {
    if (!done) return;
    if (done.chat_id) navigate(`/c/${done.chat_id}`);
    setUser(done.me);
    onDone?.();
  };

  if (done) {
    const missing = google?.available
      ? google.requested.filter((s) => !google.granted.includes(s) && SERVICE_LABEL[s])
      : [];
    return (
      <Frame>
        <div className="mb-6 flex justify-center">
          <Orb state="speaking" size={96} />
        </div>
        <h1 className="mb-2 text-center text-2xl font-semibold">You're all set.</h1>
        <p className="mb-6 text-center text-sm text-slate-400">
          {cleanName} knows a little about you now
          {done.routines > 0
            ? ` and will run ${done.routines} routine${done.routines === 1 ? "" : "s"} for you`
            : ""}
          . Change any of it later on the Memory and Routines pages.
        </p>
        {missing.length > 0 && (
          <div className="mb-4 rounded-lg border border-edge bg-panel p-4 text-sm">
            <p className="mb-2 text-slate-300">
              Let {cleanName} use your {missing.map((s) => SERVICE_LABEL[s]).join(", ")} so routines
              like the morning brief can read your day. Google will say the app isn't verified;
              choose <em>Advanced → Go to Nexus</em>.
            </p>
            <a
              href="/auth/google/connect"
              className="inline-block rounded-md border border-edge px-3 py-1.5 hover:bg-edge/60"
            >
              Connect Google
            </a>
          </div>
        )}
        <button
          onClick={enter}
          className="w-full rounded-md bg-sky-500 px-4 py-2 font-medium text-ink hover:bg-sky-400"
        >
          Say hello to {cleanName}
        </button>
      </Frame>
    );
  }

  const next = () => (step === 4 ? openRoutines() : setStep(step + 1));
  const last = step === STEPS.length - 1;

  return (
    <Frame>
      <div className="mb-5 flex items-center justify-center gap-3">
        <Orb state={saving ? "thinking" : "idle"} size={56} />
      </div>
      <ol className="mb-6 flex justify-center gap-1.5" aria-label="Setup steps">
        {STEPS.map((s, i) => (
          <li
            key={s}
            aria-current={i === step ? "step" : undefined}
            title={s}
            className={`h-1.5 w-8 rounded-full ${i <= step ? "bg-sky-400" : "bg-edge"}`}
          />
        ))}
      </ol>

      {step === 0 && (
        <Step
          title={`${first ? `Welcome, ${first}. ` : "Welcome. "}What should your assistant be called?`}
          hint="Pick one or type your own. It's yours alone, and you can change it later."
        >
          <Chips
            options={SUGGESTED_NAMES.map((n) => ({ id: n, label: n }))}
            selected={[name]}
            onToggle={(id) => setName(id)}
          />
          <input
            aria-label="Assistant name"
            value={name}
            maxLength={ASSISTANT_NAME_MAX}
            onChange={(e) => setName(e.target.value)}
            className="mt-3 w-full rounded-md border border-edge bg-panel px-3 py-2"
          />
          {name.trim() && !cleanName && (
            <p className="mt-1 text-xs text-rose-400">
              Use up to {ASSISTANT_NAME_MAX} letters, numbers, spaces, dots, hyphens or
              apostrophes.
            </p>
          )}
          {askTerms && (
            <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-lg border border-edge bg-panel p-3 text-sm">
              <input
                type="checkbox"
                checked={agreed}
                onChange={(e) => setAgreed(e.target.checked)}
                className="mt-0.5"
              />
              <span className="text-slate-300">
                I agree to the{" "}
                <a href="/terms" target="_blank" rel="noopener" className="text-sky-300 underline">
                  Terms of Service
                </a>{" "}
                and the{" "}
                <a href="/privacy" target="_blank" rel="noopener" className="text-sky-300 underline">
                  Privacy Policy
                </a>
                , including that my messages are sent to an AI provider to get answers.
              </span>
            </label>
          )}
        </Step>
      )}

      {step === 1 && (
        <Step
          title="What do you do?"
          hint={`Pick up to three. It helps ${cleanName} suggest the right things.`}
        >
          <Tiles
            icons={ICONS}
            options={[
              ...(profiles?.roles ?? []),
              { id: "other", label: "Something else", blurb: "Tell us below" },
            ]}
            selected={roles}
            onToggle={(id) =>
              setRoles(
                roles.includes(id)
                  ? roles.filter((r) => r !== id)
                  : roles.length < 3
                    ? [...roles, id]
                    : roles,
              )
            }
          />
          <input
            aria-label="Your work in your own words"
            placeholder="In your own words (optional), e.g. “PhD student in marine biology”"
            value={roleNote}
            maxLength={300}
            onChange={(e) => setRoleNote(e.target.value)}
            className="mt-3 w-full rounded-md border border-edge bg-panel px-3 py-2 text-sm"
          />
        </Step>
      )}

      {step === 2 && (
        <Step title="When does your day start?" hint="Routines like a morning brief run around then.">
          <label className="mb-3 block text-sm">
            <span className="mb-1 block text-slate-400">Time zone</span>
            <Select
              size="md"
              className="w-full"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
            >
              {timezones(timezone).map((z) => (
                <option key={z} value={z}>
                  {z.replace(/_/g, " ")}
                </option>
              ))}
            </Select>
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-slate-400">My day usually starts at</span>
            <input
              type="time"
              value={dayStart}
              onChange={(e) => e.target.value && setDayStart(e.target.value)}
              className="rounded-md border border-edge bg-panel px-3 py-2 text-base [color-scheme:dark]"
            />
          </label>
        </Step>
      )}

      {step === 3 && (
        <Step title={`What should ${cleanName} know about your world?`} hint="A little context helps it remember the people, work and tools that matter to you.">
          <Tiles
            compact
            icons={{ ...ICONS, ...INTEREST_ICONS }}
            options={profiles?.interests ?? []}
            selected={interests}
            onToggle={(id) =>
              setInterests(
                interests.includes(id) ? interests.filter((x) => x !== id) : [...interests, id],
              )
            }
          />
          <label className="mt-3 block text-sm">
            <span className="mb-1 block text-slate-400">A project or goal you’re focused on</span>
            <input
              aria-label="Current focus"
              placeholder="e.g. finishing my thesis or growing a small business"
              value={currentFocus}
              maxLength={500}
              onChange={(e) => setCurrentFocus(e.target.value)}
              className="w-full rounded-md border border-edge bg-panel px-3 py-2 text-sm"
            />
          </label>
          <label className="mt-3 block text-sm">
            <span className="mb-1 block text-slate-400">Tools or technologies you use</span>
            <input
              aria-label="Tools or technologies"
              placeholder="e.g. Python, Figma, Notion"
              value={tools}
              maxLength={500}
              onChange={(e) => setTools(e.target.value)}
              className="w-full rounded-md border border-edge bg-panel px-3 py-2 text-sm"
            />
          </label>
          <textarea
            aria-label="Anything else"
            placeholder="Anything else worth knowing? Topics you follow, your city, or how you like to work… (optional)"
            value={about}
            rows={3}
            maxLength={1000}
            onChange={(e) => setAbout(e.target.value)}
            className="mt-3 w-full rounded-md border border-edge bg-panel px-3 py-2 text-sm"
          />
          {canMember && <HistoryUpload assistant={cleanName ?? DEFAULT_ASSISTANT_NAME} />}
        </Step>
      )}

      {step === 4 && (
        <Step title="How should it answer?" hint="You can always ask for more or less in a chat.">
          <Field label="Length">
            <Chips
              options={[
                { id: "short", label: "Short and to the point" },
                { id: "balanced", label: "Balanced" },
                { id: "detailed", label: "Detailed" },
              ]}
              selected={[style.length]}
              onToggle={(id) => setStyle({ ...style, length: id as AnswerStyle["length"] })}
            />
          </Field>
          <Field label="Tone">
            <Chips
              options={[
                { id: "casual", label: "Casual" },
                { id: "professional", label: "Professional" },
              ]}
              selected={[style.tone]}
              onToggle={(id) => setStyle({ ...style, tone: id as AnswerStyle["tone"] })}
            />
          </Field>
          <Field label="Language (optional)">
            <input
              aria-label="Reply language"
              placeholder="e.g. Hindi, Spanish — leave empty to match how you write"
              value={style.language}
              maxLength={40}
              onChange={(e) => setStyle({ ...style, language: e.target.value })}
              className="w-full rounded-md border border-edge bg-panel px-3 py-2 text-sm"
            />
          </Field>
        </Step>
      )}

      {step === 5 && (
        <Step
          title="Want a few routines?"
          hint={`${cleanName} runs these on a schedule; their results are on the Routines page, not in your chats. Untick any you don't want; you can change them anytime.`}
        >
          {!canMember && (
            <p className="text-sm text-slate-400">Viewers can't have routines.</p>
          )}
          {canMember && (
            <p
              className={`mb-3 text-center text-xs ${
                Object.keys(picked ?? {}).length > MANY_ROUTINES ? "text-amber-300" : "text-slate-500"
              }`}
            >
              {Object.keys(picked ?? {}).length} picked
              {Object.keys(picked ?? {}).length > MANY_ROUTINES
                ? " · each run uses your monthly budget, so a few that matter work best"
                : " · add more any time from the Routines page"}
            </p>
          )}
          {canMember && (
            <ul className="space-y-2 sm:max-h-72 sm:overflow-y-auto sm:pr-1">
              {offered.map(({ pack, routine }) => {
                const on = picked?.[routine.key] !== undefined;
                const time = picked?.[routine.key] ?? routineTime(routine, dayStart);
                return (
                  <li
                    key={routine.key}
                    className={`rounded-lg border p-3 text-sm ${on ? "border-sky-500/50 bg-sky-500/5" : "border-edge"}`}
                  >
                    <div className="flex items-start gap-3">
                      <input
                        type="checkbox"
                        aria-label={routine.name}
                        checked={on}
                        onChange={() => {
                          const nextPicked = { ...(picked ?? {}) };
                          if (on) delete nextPicked[routine.key];
                          else nextPicked[routine.key] = time;
                          setPicked(nextPicked);
                        }}
                        className="mt-1"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">{routine.name}</span>
                          <span className="text-xs text-slate-500">{pack.label}</span>
                        </div>
                        <div className="text-xs text-slate-400">
                          {scheduleText(routine, time)}
                          {routine.needs.length > 0 &&
                            ` · uses ${routine.needs.map((n) => SERVICE_LABEL[n] ?? n).join(", ")}`}
                        </div>
                        <p className="mt-1 line-clamp-2 text-xs text-slate-500">{routine.prompt}</p>
                      </div>
                      {on && (
                        <input
                          type="time"
                          aria-label={`${routine.name} time`}
                          value={time}
                          onChange={(e) =>
                            e.target.value &&
                            setPicked({ ...(picked ?? {}), [routine.key]: e.target.value })
                          }
                          className="rounded-md border border-edge bg-panel px-2 py-1 text-base [color-scheme:dark] sm:text-xs"
                        />
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          {canMember && channels.length > 0 && (
            <div className="mt-4 space-y-2 border-t border-edge pt-3 text-sm">
              <p className="text-slate-400">Also tell me when one has something for me, by</p>
              <div className="flex flex-wrap gap-4">
                {channels.map((c) => (
                  <label key={c} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={delivery.includes(c)}
                      onChange={async (e) => {
                        const on = e.target.checked;
                        setDelivery(on ? [...delivery, c] : delivery.filter((x) => x !== c));
                        if (on && c === "push") {
                          try {
                            const state = await api.pushState();
                            if (state.public_key) await enablePush(state.public_key);
                          } catch (err) {
                            setError((err as Error).message);
                          }
                        }
                      }}
                    />
                    {c === "email"
                      ? `Email (${user.email})`
                      : c === "push"
                        ? "Notification on this device"
                        : "WhatsApp"}
                  </label>
                ))}
              </div>
              {delivery.includes("whatsapp") && (
                <input
                  aria-label="WhatsApp number"
                  placeholder="WhatsApp number with country code, e.g. +919812345678"
                  value={phone}
                  inputMode="tel"
                  onChange={(e) => setPhone(e.target.value)}
                  className="w-full rounded-md border border-edge bg-panel px-3 py-2"
                />
              )}
            </div>
          )}
        </Step>
      )}

      {error && <p className="mt-3 text-sm text-rose-400">{error}</p>}

      <div className="mt-6 flex items-center gap-2">
        {step > 0 && (
          <button
            onClick={() => setStep(step - 1)}
            className="flex items-center gap-1 rounded-md px-3 py-2 text-sm text-slate-400 hover:text-slate-200"
          >
            <ArrowLeft size={15} /> Back
          </button>
        )}
        {step > 0 && (
          <button
            onClick={() => finish(true)}
            disabled={saving}
            className="ml-auto rounded-md px-3 py-2 text-sm text-slate-500 hover:text-slate-300"
          >
            Skip setup
          </button>
        )}
        <button
          onClick={() => (last ? finish() : next())}
          disabled={saving || (step === 0 && (!cleanName || !agreed)) || !profiles}
          className={`flex items-center gap-1.5 rounded-md bg-sky-500 px-4 py-2 font-medium text-ink hover:bg-sky-400 disabled:opacity-50 ${step === 0 ? "ml-auto" : ""}`}
        >
          {last ? (
            <>
              <Check size={16} /> {saving ? "Setting up…" : "Finish"}
            </>
          ) : (
            <>
              Continue <ArrowRight size={16} />
            </>
          )}
        </button>
      </div>
    </Frame>
  );
}

/** Optional: start from a ChatGPT or Claude export. It uploads and is read in the background,
 * so nobody waits on this page for it. */
function HistoryUpload({ assistant }: { assistant: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [sent, setSent] = useState<{ name: string; state: "sending" | "in" | "failed" } | null>(null);
  const pick = (file: File) => {
    setSent({ name: file.name, state: "sending" });
    void uploadInBackground(file).then((ok) =>
      setSent((s) => (s ? { ...s, state: ok ? "in" : "failed" } : s)),
    );
  };
  return (
    <div className="mt-3 rounded-md border border-dashed border-edge px-3 py-2 text-sm">
      <div className="flex items-center gap-2">
        <span className="text-slate-400">Used ChatGPT or Claude before? Bring that history along.</span>
        <button
          type="button"
          onClick={() => input.current?.click()}
          className="ml-auto shrink-0 rounded-md border border-edge px-2.5 py-1 text-xs hover:bg-white/5"
        >
          Upload export
        </button>
        <input
          ref={input}
          type="file"
          accept=".zip,.json"
          className="hidden"
          aria-label="Chat history export"
          onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])}
        />
      </div>
      <p className="mt-1 text-xs text-slate-500">
        {sent?.state === "sending"
          ? `Uploading ${sent.name} in the background. Carry on, there's no need to wait.`
          : sent?.state === "in"
            ? `${sent.name} is in. ${assistant} reads it in the background and you can follow along on the Knowledge page.`
            : sent?.state === "failed"
              ? `${sent.name} didn't upload. You can try again later on the Knowledge page.`
              : "The zip from Settings, then Export, in either app. Only your own messages are kept, read on this machine."}
      </p>
    </div>
  );
}

function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full items-start justify-center overflow-y-auto px-4 py-10 sm:items-center">
      <div className="w-full max-w-2xl">{children}</div>
    </div>
  );
}

function Step({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <section>
      <h1 className="mb-1 text-center text-2xl font-semibold">{title}</h1>
      <p className="mb-5 text-center text-sm text-slate-400">{hint}</p>
      <div>{children}</div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="mb-4">
      <div className="mb-1.5 text-sm text-slate-400">{label}</div>
      {children}
    </div>
  );
}

/** Choices as a grid of tiles with an icon and a line on what each means: tidier than a wrap
 * of pills when there are many. */
function Tiles({
  options,
  selected,
  onToggle,
  icons,
  compact = false,
}: {
  options: { id: string; label: string; blurb?: string }[];
  selected: string[];
  onToggle: (id: string) => void;
  icons: Record<string, LucideIcon>;
  compact?: boolean;
}) {
  return (
    <div className={`grid gap-2 ${compact ? "grid-cols-2 sm:grid-cols-3" : "grid-cols-1 sm:grid-cols-2"}`}>
      {options.map((o) => {
        const on = selected.includes(o.id);
        const Icon = icons[o.id] ?? Sparkles;
        return (
          <button
            key={o.id}
            type="button"
            aria-pressed={on}
            onClick={() => onToggle(o.id)}
            className={`group relative flex items-center gap-3 rounded-xl border px-3 text-left transition ${
              compact ? "py-2.5" : "py-3"
            } ${
              on
                ? "border-sky-400/80 bg-sky-500/10 shadow-[0_0_0_1px_rgb(56_189_248_/_0.35)]"
                : "border-edge bg-panel hover:border-slate-600 hover:bg-edge/40"
            }`}
          >
            <span
              className={`grid shrink-0 place-items-center rounded-lg ${compact ? "size-8" : "size-9"} ${
                on ? "bg-sky-500/20 text-sky-200" : "bg-edge/70 text-slate-300 group-hover:text-slate-100"
              }`}
            >
              <Icon size={compact ? 16 : 18} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">{o.label}</span>
              {!compact && o.blurb && (
                <span className="block truncate text-xs text-slate-500">{o.blurb}</span>
              )}
            </span>
            {on && <Check size={15} className="shrink-0 text-sky-300" />}
          </button>
        );
      })}
    </div>
  );
}

function Chips({
  options,
  selected,
  onToggle,
}: {
  options: { id: string; label: string; blurb?: string }[];
  selected: string[];
  onToggle: (id: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((o) => {
        const on = selected.includes(o.id);
        return (
          <button
            key={o.id}
            type="button"
            aria-pressed={on}
            title={o.blurb || undefined}
            onClick={() => onToggle(o.id)}
            className={`rounded-full border px-3 py-1.5 text-sm transition ${
              on
                ? "border-sky-400 bg-sky-500/15 text-sky-200"
                : "border-edge text-slate-300 hover:bg-edge/60"
            }`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
