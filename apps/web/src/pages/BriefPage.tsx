import {
  AlertTriangle,
  ArrowRight,
  Bell,
  CalendarDays,
  Check,
  Cloud,
  CloudDrizzle,
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSnow,
  CloudSun,
  Inbox,
  ListTodo,
  Loader2,
  MapPin,
  RefreshCw,
  Send,
  Settings2,
  Sun,
  Umbrella,
  Video,
  Wallet,
  Wind,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { toast } from "../components/Feedback";
import { Guide, GuideHeading } from "../components/Guide";
import { Select } from "../components/Select";
import { api } from "../lib/api";
import {
  BRIEF_SECTIONS,
  clockText,
  nextSend,
  type Brief,
  type BriefEvent,
  type BriefPage as BriefData,
  type BriefSection,
  type BriefSettings,
  type BriefTask,
  type BriefWeather,
  type Place,
} from "../lib/brief";
import { formatAgo } from "../lib/format";
import { rupees } from "../lib/money";

/** The morning brief: the day at a glance, and when and where it arrives each morning. */
export function BriefPage() {
  const [data, setData] = useState<BriefData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api.brief());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 py-5 sm:py-6">
        <div className="mb-1 flex items-center gap-2">
          <h1 className="text-xl font-semibold">Brief</h1>
          <button
            onClick={() => void load()}
            disabled={loading}
            className="ml-auto flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-sm text-slate-300 hover:bg-edge disabled:opacity-60"
            title="Build the brief again with the latest calendar, tasks and money"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> <span className="hidden sm:inline">Refresh</span>
          </button>
          <button
            onClick={() => setEditing(true)}
            disabled={!data}
            className="flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-sm text-slate-300 hover:bg-edge disabled:opacity-60"
          >
            <Settings2 size={14} /> <span className="hidden sm:inline">Settings</span>
          </button>
        </div>
        <p className="mb-4 text-sm text-slate-400">Your day at a glance, built fresh each time you open it.</p>
        <BriefGuide />
        {error && !data && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>
        )}
        {!data && !error && (
          <div className="flex items-center gap-2 py-8 text-sm text-slate-400">
            <Loader2 size={16} className="animate-spin" /> Putting your day together…
          </div>
        )}
        {data && (
          <>
            <Delivery data={data} onEdit={() => setEditing(true)} />
            <Hero brief={data.brief} />
            <div className="grid gap-4 md:grid-cols-2">
              {data.brief.sections.includes("calendar") && <CalendarCard brief={data.brief} />}
              {data.brief.sections.includes("tasks") && <TasksCard brief={data.brief} onChanged={load} />}
              {data.brief.sections.includes("weather") && <WeatherCard brief={data.brief} onPick={() => setEditing(true)} />}
              {data.brief.sections.includes("money") && data.money_available && <MoneyCard brief={data.brief} />}
            </div>
            <p className="mt-4 text-xs text-slate-500">Built {formatAgo(data.brief.generated_at)}.</p>
          </>
        )}
        {editing && data && (
          <SettingsDialog
            data={data}
            onClose={() => setEditing(false)}
            onSaved={(settings) => {
              setData((cur) => (cur ? { ...cur, settings } : cur));
              void load();
            }}
          />
        )}
      </div>
    </div>
  );
}

function BriefGuide() {
  return (
    <Guide title="How the brief works">
      <div>
        <GuideHeading>What's in it</GuideHeading>
        Today's events from Google Calendar, tasks from the Organiser that are overdue, due or in hand, the weather and air where you
        are, and, if Money is set up, what's safe to spend, payments due in the next three days and the next step to stay safe.
      </div>
      <div>
        <GuideHeading>Every morning</GuideHeading>
        Switch it on in Settings and pick a time. It arrives as a notice here and on your phone, and in Telegram if you've linked it.
        The phone notice only says how many events and tasks you have, never their names or amounts.
      </div>
      <div>
        <GuideHeading>Privacy</GuideHeading>
        Nothing in the brief is stored: it's built when you open this page or when it's sent. Weather comes from Open-Meteo, which only
        sees the place you pick. Money stays out of Telegram unless you include it, because Telegram isn't end-to-end encrypted.
      </div>
      <div>
        <GuideHeading>Ask for it</GuideHeading>
        Ask your assistant "what's my day?" and it reads the same brief. Tick tasks off here as you go; Refresh picks up changes.
      </div>
    </Guide>
  );
}

/** When the next one arrives, or an offer to switch it on. */
function Delivery({ data, onEdit }: { data: BriefData; onEdit: () => void }) {
  const s = data.settings;
  const next = nextSend(s);
  if (!s.enabled)
    return (
      <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-sky-500/30 bg-sky-500/10 px-4 py-3 text-sm">
        <Bell size={16} className="shrink-0 text-sky-300" />
        <span className="min-w-[12rem] flex-1 text-slate-200">Get this every morning, as a notice on your phone{data.telegram_linked ? " and in Telegram" : ""}.</span>
        <button onClick={onEdit} className="rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink hover:bg-sky-400">
          Set it up
        </button>
      </div>
    );
  const where = [s.push && "as a notice on your phone", s.telegram && data.telegram_linked && "in Telegram"].filter(Boolean).join(" and ");
  return (
    <div className="mb-4 flex items-start gap-2 text-sm text-slate-400">
      <Bell size={14} className="mt-[3px] shrink-0 text-emerald-400" aria-hidden />
      <p>
        Arrives {s.days === "weekdays" ? "on weekdays" : "every day"} at {clockText(s.send_at)}
        {where && <> {where}</>}.{next && <> Next: {next}.</>} <SendNow />
      </p>
    </div>
  );
}

function SendNow() {
  const [busy, setBusy] = useState(false);
  return (
    <button
      onClick={async () => {
        setBusy(true);
        try {
          await api.briefSendNow();
          toast("On its way. It takes a few seconds.");
        } catch (e) {
          toast((e as Error).message, "error");
        } finally {
          setBusy(false);
        }
      }}
      disabled={busy}
      className="inline-flex items-center gap-1 text-sky-300 hover:text-sky-200 disabled:opacity-60"
    >
      <Send size={13} aria-hidden /> Send me one now
    </button>
  );
}

function Hero({ brief }: { brief: Brief }) {
  const day = new Date(`${brief.date}T12:00:00`).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  return (
    <section className="mb-4 rounded-2xl border border-edge bg-gradient-to-br from-sky-500/10 via-panel to-panel p-5">
      <div className="text-sm text-slate-400">{day}</div>
      <h2 className="mt-0.5 text-2xl font-semibold text-slate-50">{brief.greeting}.</h2>
      <p className="mt-2 max-w-2xl text-base leading-relaxed text-slate-300">{brief.headline}</p>
    </section>
  );
}

function Card({ title, icon, action, children, className = "" }: { title: string; icon: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-xl border border-edge bg-panel p-4 ${className}`}>
      <h2 className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-300">
        {icon}
        {title}
        {action && <span className="ml-auto text-xs font-normal">{action}</span>}
      </h2>
      {children}
    </section>
  );
}

function Unavailable({ what }: { what: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-amber-200">
      <AlertTriangle size={14} /> Couldn't read {what} just now. Refresh to try again.
    </div>
  );
}

function Quiet({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-edge px-3 py-4 text-center text-sm text-slate-400">{children}</div>;
}

// --- Calendar ---------------------------------------------------------------------------------

function CalendarCard({ brief }: { brief: Brief }) {
  const cal = brief.calendar;
  const events = cal?.events ?? [];
  const nextIndex = events.findIndex((e) => !e.all_day && !e.past);
  return (
    <Card title="Calendar" icon={<CalendarDays size={15} className="text-sky-300" />}>
      {brief.errors.calendar ? (
        <Unavailable what="your calendar" />
      ) : !cal?.connected ? (
        <Quiet>
          Google Calendar isn't connected.{" "}
          <Link to="/settings" className="text-sky-300 hover:underline">
            Connect it in Settings
          </Link>
        </Quiet>
      ) : events.length === 0 ? (
        <Quiet>Nothing on the calendar today.</Quiet>
      ) : (
        <ol className="space-y-1">
          {events.map((e, i) => (
            <EventRow key={`${e.title}-${e.start ?? i}`} event={e} next={i === nextIndex} />
          ))}
        </ol>
      )}
    </Card>
  );
}

function EventRow({ event: e, next }: { event: BriefEvent; next: boolean }) {
  return (
    <li
      className={`flex gap-3 rounded-lg px-2 py-2 ${next ? "bg-sky-500/10 ring-1 ring-sky-500/30" : ""} ${e.past ? "opacity-50" : ""}`}
    >
      <div className="w-16 shrink-0 text-right text-xs leading-5 tabular-nums text-slate-400">
        {e.all_day ? "All day" : e.starts}
        {!e.all_day && e.ends && <div className="text-slate-600">{e.ends}</div>}
      </div>
      <div className="min-w-0 flex-1">
        <div className={`truncate text-sm ${e.past ? "text-slate-400" : "text-slate-100"}`}>
          {e.link ? (
            <a href={e.link} target="_blank" rel="noreferrer" className="hover:underline">
              {e.title}
            </a>
          ) : (
            e.title
          )}
        </div>
        {(e.where || e.others > 0) && (
          <div className="mt-0.5 flex min-w-0 items-center gap-1 text-xs text-slate-500">
            {e.where && (
              <>
                <MapPin size={11} className="shrink-0" /> <span className="truncate">{e.where}</span>
              </>
            )}
            {e.others > 0 && <span className="shrink-0">{e.where ? " · " : ""}with {e.others} other{e.others === 1 ? "" : "s"}</span>}
          </div>
        )}
      </div>
      {e.video && !e.past && (
        <a
          href={e.video}
          target="_blank"
          rel="noreferrer"
          className="flex h-7 shrink-0 items-center gap-1 self-center rounded-md border border-edge px-2 text-xs text-slate-300 hover:bg-edge"
        >
          <Video size={12} /> Join
        </a>
      )}
    </li>
  );
}

// --- Tasks ------------------------------------------------------------------------------------

function TasksCard({ brief, onChanged }: { brief: Brief; onChanged: () => void }) {
  const t = brief.tasks;
  const groups: { label: string; items: BriefTask[]; tone: string }[] = t
    ? [
        { label: "Overdue", items: t.overdue, tone: "text-red-300" },
        { label: "Due today", items: t.due, tone: "text-amber-200" },
        { label: "In progress", items: t.doing, tone: "text-sky-300" },
        { label: "This week's focus", items: t.focus, tone: "text-violet-300" },
      ].filter((g) => g.items.length)
    : [];
  return (
    <Card
      title="Tasks"
      icon={<ListTodo size={15} className="text-amber-300" />}
      action={
        <Link to="/organiser" className="text-sky-300 hover:underline">
          Organiser
        </Link>
      }
    >
      {brief.errors.tasks ? (
        <Unavailable what="your tasks" />
      ) : !t ? null : (
        <>
          {groups.length === 0 ? (
            <Quiet>Nothing due today.</Quiet>
          ) : (
            <div className="space-y-3">
              {groups.map((g) => (
                <div key={g.label}>
                  <div className={`mb-1 text-xs font-medium ${g.tone}`}>{g.label}</div>
                  <ul>
                    {g.items.map((task) => (
                      <TaskTick key={`${g.label}-${task.id}`} task={task} today={brief.date} onChanged={onChanged} />
                    ))}
                  </ul>
                </div>
              ))}
              {t.more > 0 && <div className="text-xs text-slate-500">And {t.more} more in the Organiser.</div>}
            </div>
          )}
          {(t.inbox > 0 || (t.monday && !t.review_done)) && (
            <div className="mt-3 flex flex-wrap gap-2 border-t border-edge pt-3 text-xs">
              {t.inbox > 0 && (
                <Link to="/organiser/inbox" className="flex items-center gap-1 rounded-full bg-edge px-2.5 py-1 text-slate-300 hover:text-white">
                  <Inbox size={12} /> {t.inbox} in the Inbox to sort
                </Link>
              )}
              {t.monday && !t.review_done && (
                <Link to="/organiser/review" className="flex items-center gap-1 rounded-full bg-edge px-2.5 py-1 text-slate-300 hover:text-white">
                  Last week's review isn't done <ArrowRight size={12} />
                </Link>
              )}
            </div>
          )}
        </>
      )}
    </Card>
  );
}

function TaskTick({ task, today, onChanged }: { task: BriefTask; today: string; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const late = task.due_on && task.due_on < today;
  const tick = async () => {
    setBusy(true);
    try {
      await api.orgUpdate(task.id, { status: "done" });
      setDone(true);
      toast(`Done: ${task.title}`);
      onChanged();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const canTick = task.type === "task";
  return (
    <li className="group flex items-start gap-2.5 rounded-lg px-1.5 py-1.5 hover:bg-edge/40">
      {canTick ? (
        <button
          onClick={tick}
          disabled={busy || done}
          role="checkbox"
          aria-checked={done}
          aria-label={`Mark ${task.title} done`}
          className={`mt-0.5 flex size-[18px] shrink-0 items-center justify-center rounded-full border transition-colors ${
            done ? "border-emerald-500 bg-emerald-500 text-ink" : "border-slate-500 hover:border-emerald-400"
          }`}
        >
          {busy ? <Loader2 size={11} className="animate-spin" /> : done && <Check size={12} strokeWidth={3} />}
        </button>
      ) : (
        <span className="mt-1.5 size-2 shrink-0 rounded-full bg-violet-400" aria-hidden />
      )}
      <Link to={`/organiser/item/${task.id}`} className={`min-w-0 flex-1 text-sm ${done ? "text-slate-500 line-through" : "text-slate-100"} hover:underline`}>
        {task.title}
      </Link>
      {late && task.due_on && (
        <span className="shrink-0 text-xs text-red-300">
          {new Date(`${task.due_on}T12:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}
        </span>
      )}
    </li>
  );
}

// --- Weather ----------------------------------------------------------------------------------

function skyIcon(w: BriefWeather, size = 28) {
  const c = w.code ?? 0;
  const props = { size, className: "text-sky-200", "aria-hidden": true } as const;
  if (c >= 95) return <CloudLightning {...props} />;
  if (c >= 71 && c <= 86 && !(c >= 80 && c <= 82)) return <CloudSnow {...props} />;
  if ((c >= 61 && c <= 67) || (c >= 80 && c <= 82)) return <CloudRain {...props} />;
  if (c >= 51 && c <= 57) return <CloudDrizzle {...props} />;
  if (c === 45 || c === 48) return <CloudFog {...props} />;
  if (c === 3) return <Cloud {...props} />;
  if (c === 1 || c === 2) return <CloudSun {...props} />;
  return <Sun {...props} className="text-amber-200" />;
}

const AIR_TONE: Record<string, string> = {
  good: "bg-emerald-500/15 text-emerald-200",
  moderate: "bg-yellow-500/15 text-yellow-100",
  "unhealthy for sensitive groups": "bg-orange-500/15 text-orange-200",
  unhealthy: "bg-red-500/15 text-red-200",
  "very unhealthy": "bg-fuchsia-500/15 text-fuchsia-200",
  hazardous: "bg-rose-700/30 text-rose-100",
};

function WeatherCard({ brief, onPick }: { brief: Brief; onPick: () => void }) {
  const w = brief.weather;
  const place = brief.place?.split(",")[0];
  return (
    <Card title={place ? `Weather in ${place}` : "Weather"} icon={<CloudSun size={15} className="text-sky-300" />}>
      {brief.errors.weather ? (
        <Unavailable what="the weather" />
      ) : !brief.place ? (
        <Quiet>
          <button onClick={onPick} className="text-sky-300 hover:underline">
            Pick your town or city
          </button>{" "}
          for the weather and air quality.
        </Quiet>
      ) : !w ? (
        <Unavailable what="the weather" />
      ) : (
        <div>
          <div className="flex items-center gap-3">
            {skyIcon(w)}
            <div>
              <div className="text-2xl font-semibold tabular-nums text-slate-50">
                {w.now_c ?? w.high_c}°<span className="ml-1 text-sm font-normal text-slate-400">now</span>
              </div>
              <div className="text-sm text-slate-400">
                {w.low_c}° to {w.high_c}°{w.sky && <>, {w.sky}</>}
              </div>
            </div>
          </div>
          <div className="mt-3 space-y-1.5 text-sm">
            {w.rain_from ? (
              <div className="flex items-center gap-2 text-sky-200">
                <Umbrella size={14} /> Rain likely from {w.rain_from} ({w.rain_chance}%). Take an umbrella.
              </div>
            ) : (
              w.rain_chance != null && (
                <div className="flex items-center gap-2 text-slate-400">
                  <Umbrella size={14} /> {w.rain_chance}% chance of rain{w.rain_chance >= 50 ? " earlier today" : ""}.
                </div>
              )
            )}
            {w.aqi != null && w.air && (
              <div className="flex items-center gap-2 text-slate-400">
                <Wind size={14} /> Air quality
                <span className={`rounded-full px-2 py-0.5 text-xs ${AIR_TONE[w.air] ?? "bg-edge text-slate-300"}`}>
                  {w.air} · {w.aqi}
                </span>
              </div>
            )}
            {w.aqi != null && w.aqi > 150 && <div className="text-xs text-slate-500">Worth a mask outdoors and keeping windows shut.</div>}
          </div>
        </div>
      )}
    </Card>
  );
}

// --- Money ------------------------------------------------------------------------------------

const VERDICT: Record<string, { label: string; tone: string }> = {
  good: { label: "Safe", tone: "text-emerald-300" },
  watch: { label: "Worth a look", tone: "text-amber-200" },
  act: { label: "Needs action", tone: "text-red-300" },
};

function MoneyCard({ brief }: { brief: Brief }) {
  const m = brief.money;
  return (
    <Card
      title="Money"
      icon={<Wallet size={15} className="text-emerald-300" />}
      action={
        m && (
          <Link to="/money/health" className={`${VERDICT[m.verdict]?.tone ?? "text-slate-300"} hover:underline`}>
            {VERDICT[m.verdict]?.label ?? m.verdict}
          </Link>
        )
      }
    >
      {brief.errors.money ? (
        <Unavailable what="Money" />
      ) : !m ? (
        <Quiet>
          Money isn't set up yet.{" "}
          <Link to="/money" className="text-sky-300 hover:underline">
            Start in Money
          </Link>
        </Quiet>
      ) : (
        <div className="space-y-3">
          {m.safe.known ? (
            <div>
              <div className="text-xs text-slate-400">{m.safe.left_paise >= 0 ? "Safe to spend this month" : "Past this month's safe amount"}</div>
              <div className={`text-2xl font-semibold tabular-nums ${m.safe.left_paise >= 0 ? "text-slate-50" : "text-red-300"}`}>
                {rupees(Math.abs(m.safe.left_paise), { whole: true })}
                {m.safe.left_paise >= 0 && (
                  <span className="ml-2 text-sm font-normal text-slate-400">{rupees(m.safe.per_day_paise, { whole: true })} a day</span>
                )}
              </div>
              <div className="text-xs text-slate-500">
                {m.safe.days_left} day{m.safe.days_left === 1 ? "" : "s"} left in the month
              </div>
            </div>
          ) : (
            <div className="text-sm text-slate-400">Safe to spend shows once Money knows your income.</div>
          )}
          {m.due.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium text-slate-300">Due in the next three days</div>
              <ul className="space-y-1">
                {m.due.map((u) => (
                  <li key={`${u.kind}-${u.label}-${u.on}`} className="flex items-baseline gap-2 text-sm">
                    <span className="min-w-0 flex-1 truncate text-slate-200">{u.label}</span>
                    <span className={`shrink-0 text-xs ${u.overdue ? "text-red-300" : "text-slate-500"}`}>{dueWord(u.on, brief.date, u.overdue)}</span>
                    <span className="shrink-0 tabular-nums text-slate-100">{rupees(u.amount_paise, { whole: true })}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {m.yesterday.count > 0 && (
            <div className="text-sm text-slate-400">
              Yesterday: <span className="tabular-nums text-slate-200">{rupees(m.yesterday.spent_paise, { whole: true })}</span> across{" "}
              {m.yesterday.count} payment{m.yesterday.count === 1 ? "" : "s"}.
            </div>
          )}
          {(m.advice ?? []).map((a) => (
            <Link
              key={a.title}
              to={a.url}
              className="flex items-center gap-2 rounded-lg border border-amber-400/30 bg-amber-400/5 px-3 py-2 text-sm text-slate-200 hover:bg-amber-400/10"
            >
              <span className="min-w-0 flex-1">To sort out: {a.title}</span>
              <ArrowRight size={14} className="shrink-0 text-slate-500" />
            </Link>
          ))}
          {m.next_step && m.next_step.status !== "good" && (
            <Link
              to="/money/health"
              className="flex items-center gap-2 rounded-lg border border-edge px-3 py-2 text-sm text-slate-300 hover:bg-edge/50"
            >
              <span className="min-w-0 flex-1">
                {m.next_step.status === "unknown" ? (
                  <>
                    Next for your safety: tell Money about your <span className="text-slate-100">{m.next_step.title.toLowerCase()}</span>
                  </>
                ) : (
                  <>
                    Next for your safety: <span className="text-slate-100">{m.next_step.title}</span>{" "}
                    <span className="text-slate-500">({m.next_step.value})</span>
                  </>
                )}
              </span>
              <ArrowRight size={14} className="shrink-0 text-slate-500" />
            </Link>
          )}
        </div>
      )}
    </Card>
  );
}

function dueWord(on: string, today: string, overdue: boolean): string {
  if (overdue) return "overdue";
  if (on === today) return "today";
  const d = new Date(`${on}T12:00:00`);
  const t = new Date(`${today}T12:00:00`);
  const days = Math.round((d.getTime() - t.getTime()) / 86400000);
  if (days === 1) return "tomorrow";
  return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}

// --- Settings ---------------------------------------------------------------------------------

function SettingsDialog({ data, onClose, onSaved }: { data: BriefData; onClose: () => void; onSaved: (s: BriefSettings) => void }) {
  const [s, setS] = useState<BriefSettings>(data.settings);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof BriefSettings>(k: K, v: BriefSettings[K]) => setS((cur) => ({ ...cur, [k]: v }));
  const sections = BRIEF_SECTIONS.filter((x) => x.key !== "money" || data.money_available);
  const toggleSection = (key: BriefSection, on: boolean) =>
    set("sections", on ? [...s.sections, key] : s.sections.filter((x) => x !== key));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const save = async () => {
    if (!s.sections.length) return setError("Keep at least one part of the brief.");
    if (!s.push && !(s.telegram && data.telegram_linked) && s.enabled)
      return setError("Pick at least one way for it to arrive, or switch it off.");
    setBusy(true);
    setError(null);
    try {
      const saved = await api.briefSaveSettings({
        enabled: s.enabled,
        send_at: s.send_at,
        days: s.days,
        push: s.push,
        telegram: s.telegram,
        telegram_money: s.telegram_money,
        place: s.place,
        lat: s.lat,
        lon: s.lon,
        sections: s.sections,
      });
      toast(saved.enabled ? `Saved. ${nextSend(saved) ?? ""}`.trim() : "Saved");
      onSaved(saved);
      onClose();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 sm:items-center sm:p-4"
      onPointerDown={(e) => e.target === e.currentTarget && !busy && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Brief settings"
        className="max-h-[92dvh] w-full overflow-y-auto rounded-t-2xl border border-edge bg-panel p-5 shadow-2xl shadow-black/50 sm:max-w-lg sm:rounded-xl"
      >
        <div className="mb-1 flex items-start gap-2">
          <h2 className="flex-1 font-semibold text-slate-100">Brief settings</h2>
          <button onClick={onClose} className="-m-1 p-1 text-slate-400 hover:text-white" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <p className="text-sm text-slate-400">When it arrives, where, and what's in it.</p>

        <div className="mt-4 space-y-5">
          <label className="flex items-center gap-3 rounded-lg border border-edge px-3 py-2.5">
            <span className="flex-1 text-sm">
              <span className="block text-slate-100">Send it every morning</span>
              <span className="block text-xs text-slate-500">The page works either way.</span>
            </span>
            <Switch checked={s.enabled} onChange={(v) => set("enabled", v)} label="Send it every morning" />
          </label>

          <fieldset disabled={!s.enabled} className="grid grid-cols-2 gap-3 disabled:opacity-50">
            <label className="block text-sm text-slate-300">
              <span className="mb-1 block">At</span>
              <input
                type="time"
                value={s.send_at}
                onChange={(e) => e.target.value && set("send_at", e.target.value)}
                className="w-full rounded-md border border-edge bg-ink px-2.5 py-1.5 text-base text-slate-100 focus:border-sky-500 focus:outline-none sm:text-sm"
              />
            </label>
            <label className="block text-sm text-slate-300">
              <span className="mb-1 block">On</span>
              <Select value={s.days} onChange={(e) => set("days", e.target.value as BriefSettings["days"])} className="w-full">
                <option value="every">Every day</option>
                <option value="weekdays">Weekdays</option>
              </Select>
            </label>
            <div className="col-span-2 space-y-2 text-sm">
              <Check2 checked={s.push} onChange={(v) => set("push", v)} label="A notice here and on your phone" hint="Counts only: no event names or amounts on the lock screen." />
              <Check2
                checked={s.telegram && data.telegram_linked}
                disabled={!data.telegram_linked}
                onChange={(v) => set("telegram", v)}
                label="The whole brief in Telegram"
                hint={
                  data.telegram_linked ? (
                    "With event and task names."
                  ) : (
                    <>
                      Link Telegram in{" "}
                      <Link to="/settings" className="text-sky-300 hover:underline">
                        Settings
                      </Link>{" "}
                      first.
                    </>
                  )
                }
              />
              {data.money_available && data.telegram_linked && s.telegram && (
                <div className="pl-6">
                  <Check2
                    checked={s.telegram_money}
                    onChange={(v) => set("telegram_money", v)}
                    label="Include money in Telegram"
                    hint="Off by default: Telegram messages aren't end-to-end encrypted."
                  />
                </div>
              )}
            </div>
          </fieldset>

          <PlacePicker
            place={s.place}
            onPick={(p) => setS((cur) => ({ ...cur, place: p ? `${p.name}, ${p.region}` : null, lat: p?.lat ?? null, lon: p?.lon ?? null }))}
          />

          <div>
            <div className="mb-1.5 text-sm text-slate-300">What's in it</div>
            <div className="grid gap-2 sm:grid-cols-2">
              {sections.map((x) => (
                <Check2 key={x.key} checked={s.sections.includes(x.key)} onChange={(v) => toggleSection(x.key, v)} label={x.label} hint={x.hint} />
              ))}
            </div>
          </div>
        </div>

        {error && <div className="mt-4 rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-200">{error}</div>}
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-slate-300 hover:bg-edge">
            Cancel
          </button>
          <button
            onClick={save}
            disabled={busy}
            className="flex items-center gap-1.5 rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-60"
          >
            {busy && <Loader2 size={14} className="animate-spin" />} Save
          </button>
        </div>
      </div>
    </div>
  );
}

function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <span className="relative shrink-0">
      <input type="checkbox" role="switch" checked={checked} onChange={(e) => onChange(e.target.checked)} className="peer sr-only" aria-label={label} />
      <span className="block h-5 w-9 rounded-full bg-edge transition peer-checked:bg-sky-500 peer-focus-visible:ring-1 peer-focus-visible:ring-sky-300 after:absolute after:top-0.5 after:left-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:transition peer-checked:after:translate-x-4" />
    </span>
  );
}

function Check2({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className={`flex items-start gap-2 text-sm text-slate-300 ${disabled ? "opacity-60" : ""}`}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} className="mt-0.5 accent-sky-500" />
      <span>
        {label}
        {hint && <span className="block text-xs text-slate-500">{hint}</span>}
      </span>
    </label>
  );
}

function PlacePicker({ place, onPick }: { place: string | null; onPick: (p: Place | null) => void }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Place[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    window.clearTimeout(timer.current);
    if (q.trim().length < 2) {
      setFound(null);
      return;
    }
    timer.current = window.setTimeout(async () => {
      setBusy(true);
      try {
        setFound((await api.briefPlaces(q.trim())).places);
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    }, 350);
    return () => window.clearTimeout(timer.current);
  }, [q]);

  return (
    <div>
      <div className="mb-1 text-sm text-slate-300">Weather for</div>
      {place ? (
        <div className="flex items-center gap-2 rounded-md border border-edge bg-ink px-2.5 py-1.5 text-sm">
          <MapPin size={14} className="shrink-0 text-sky-300" />
          <span className="min-w-0 flex-1 truncate text-slate-100">{place}</span>
          <button onClick={() => onPick(null)} className="text-xs text-slate-400 hover:text-white">
            Change
          </button>
        </div>
      ) : (
        <>
          <div className="relative">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Type your town or city"
              aria-label="Search for your town or city"
              className="w-full rounded-md border border-edge bg-ink px-2.5 py-1.5 text-base text-slate-100 placeholder:text-slate-600 focus:border-sky-500 focus:outline-none sm:text-sm"
            />
            {busy && <Loader2 size={14} className="absolute top-2.5 right-2.5 animate-spin text-slate-500" />}
          </div>
          {error && <div className="mt-1 text-xs text-red-300">{error}</div>}
          {found && (
            <ul className="mt-1 max-h-48 overflow-y-auto rounded-md border border-edge bg-ink">
              {found.length === 0 && <li className="px-3 py-2 text-sm text-slate-500">No places found.</li>}
              {found.map((p) => (
                <li key={`${p.lat},${p.lon}`}>
                  <button
                    onClick={() => {
                      onPick(p);
                      setQ("");
                      setFound(null);
                    }}
                    className="flex w-full items-baseline gap-2 px-3 py-2 text-left text-sm hover:bg-edge"
                  >
                    <span className="text-slate-100">{p.name}</span>
                    <span className="truncate text-xs text-slate-500">{p.region}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-1 text-xs text-slate-500">Only this place goes to Open-Meteo, the free weather service.</div>
        </>
      )}
    </div>
  );
}
