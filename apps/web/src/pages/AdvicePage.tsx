import {
  AlarmClock,
  ArrowRight,
  Check,
  CheckCircle2,
  CircleAlert,
  CreditCard,
  Eye,
  LayoutList,
  Loader2,
  Receipt,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  TrendingUp,
  X,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { toast } from "../components/Feedback";
import { Guide, GuideHeading } from "../components/Guide";
import { api } from "../lib/api";
import { formatDate } from "../lib/format";
import { type Advice, type AdviceAction, type AdviceArea, type AdviceItem, type AdviceLevel } from "../lib/money";

const LEVELS: { level: AdviceLevel; title: string; hint: string; icon: LucideIcon; tone: string }[] = [
  { level: "act", title: "Do now", hint: "Costs money or risks a missed payment if left.", icon: CircleAlert, tone: "text-amber-300" },
  { level: "look", title: "Worth a look", hint: "Not urgent, but a quick decision helps.", icon: Eye, tone: "text-sky-300" },
  { level: "good", title: "Going well", hint: "Things you're already doing right.", icon: CheckCircle2, tone: "text-emerald-300" },
];

const AREAS: Record<AdviceArea, { label: string; icon: LucideIcon }> = {
  cards: { label: "Cards", icon: CreditCard },
  subscriptions: { label: "Subscriptions", icon: Receipt },
  spending: { label: "Spending", icon: TrendingUp },
  safety: { label: "Safety", icon: ShieldCheck },
  life: { label: "Organiser", icon: LayoutList },
};

const SNOOZES = [
  { days: 1, label: "Tomorrow" },
  { days: 7, label: "A week" },
  { days: 30, label: "A month" },
];

/** Advice: what in your own money and Organiser data deserves a decision, most urgent first. */
export function AdvicePage() {
  const [data, setData] = useState<Advice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // The last thing set aside, so it can be undone in one tap.
  const [undo, setUndo] = useState<{ key: string; title: string; what: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api.advice());
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

  const mark = async (item: { key: string; title: string }, action: AdviceAction, days?: number) => {
    try {
      setData(await api.adviceState(item.key, action, days));
      if (action === "restore") setUndo(null);
      else {
        const what = action === "done" ? "Marked done" : action === "dismissed" ? "Dismissed" : `Snoozed for ${days === 1 ? "a day" : `${days} days`}`;
        setUndo({ key: item.key, title: item.title, what });
      }
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl px-4 py-5 sm:py-6">
        <div className="mb-1 flex items-center gap-2">
          <h1 className="text-xl font-semibold">Advice</h1>
          <button
            onClick={() => void load()}
            disabled={loading}
            className="ml-auto flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-sm text-slate-300 hover:bg-edge disabled:opacity-60"
            title="Look through your data again"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> <span className="hidden sm:inline">Refresh</span>
          </button>
        </div>
        <p className="mb-4 text-sm text-slate-400">
          What in your own accounts, cards, subscriptions and Organiser deserves a decision, most urgent first.
        </p>
        <AdviceGuide />
        {error && !data && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>
        )}
        {!data && !error && (
          <div className="flex items-center gap-2 py-8 text-sm text-slate-400">
            <Loader2 size={16} className="animate-spin" /> Looking through your data…
          </div>
        )}
        {undo && (
          <div
            role="status"
            className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-edge bg-panel px-4 py-2.5 text-sm text-slate-300"
          >
            <span className="min-w-0 flex-1 truncate">
              {undo.what}: {undo.title}
            </span>
            <button onClick={() => void mark(undo, "restore")} className="flex items-center gap-1 text-sky-300 hover:text-sky-200">
              <RotateCcw size={13} /> Undo
            </button>
            <button onClick={() => setUndo(null)} aria-label="Close" className="text-slate-500 hover:text-slate-300">
              <X size={14} />
            </button>
          </div>
        )}
        {data && (
          <>
            {!data.money && (
              <p className="mb-4 rounded-xl border border-edge bg-panel/60 px-4 py-3 text-sm text-slate-400">
                Only Organiser advice shows here. Set up Money to get advice on cards, subscriptions and spending.
              </p>
            )}
            {data.items.length === 0 ? (
              <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-6 text-center text-sm text-emerald-100">
                <CheckCircle2 size={22} className="mx-auto mb-2 text-emerald-300" />
                Nothing needs you right now. Nexus checks again every hour as new alerts arrive.
              </div>
            ) : (
              LEVELS.map(({ level, title, hint, icon: Icon, tone }) => {
                const items = data.items.filter((i) => i.level === level);
                if (!items.length) return null;
                return (
                  <section key={level} className="mb-6" aria-labelledby={`advice-${level}`}>
                    <div className="mb-2 flex items-baseline gap-2">
                      <Icon size={16} className={`shrink-0 self-center ${tone}`} aria-hidden />
                      <h2 id={`advice-${level}`} className="font-medium text-slate-100">
                        {title} <span className="text-slate-500">· {items.length}</span>
                      </h2>
                      <span className="hidden text-xs text-slate-500 sm:inline">{hint}</span>
                    </div>
                    <ul className="grid gap-2">
                      {items.map((item) => (
                        <AdviceCard key={item.key} item={item} onMark={mark} />
                      ))}
                    </ul>
                  </section>
                );
              })
            )}
            {data.set_aside.length > 0 && <SetAside data={data} onRestore={(i) => void mark(i, "restore")} />}
          </>
        )}
      </div>
    </div>
  );
}

function AdviceCard({
  item,
  onMark,
}: {
  item: AdviceItem;
  onMark: (item: AdviceItem, action: AdviceAction, days?: number) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [snoozing, setSnoozing] = useState(false);
  const area = AREAS[item.area] ?? AREAS.safety;
  const AreaIcon = area.icon;
  const edge =
    item.level === "act" ? "border-amber-500/30" : item.level === "good" ? "border-emerald-500/20" : "border-edge";
  const run = async (action: AdviceAction, days?: number) => {
    setBusy(true);
    try {
      await onMark(item, action, days);
    } finally {
      setBusy(false);
      setSnoozing(false);
    }
  };
  const button = "flex items-center gap-1 rounded-md px-2 py-1 text-xs text-slate-400 hover:bg-edge hover:text-slate-200 disabled:opacity-60";
  return (
    <li className={`rounded-xl border ${edge} bg-panel px-4 py-3`}>
      <div className="flex items-start gap-3">
        <AreaIcon size={16} className="mt-0.5 shrink-0 text-slate-400" aria-label={area.label} />
        <div className="min-w-0 flex-1">
          <h3 className="font-medium text-slate-100">{item.title}</h3>
          <p className="mt-1 text-sm text-slate-300">{item.body}</p>
          {item.why && <p className="mt-1 text-xs text-slate-500">How Nexus knows: {item.why}</p>}
          <div className="mt-2 flex flex-wrap items-center gap-1">
            {item.url && (
              <Link
                to={item.url}
                className="mr-1 flex items-center gap-1 rounded-md bg-sky-500/15 px-2.5 py-1 text-xs font-medium text-sky-200 hover:bg-sky-500/25"
              >
                {item.action || "Open"} <ArrowRight size={12} />
              </Link>
            )}
            {item.level !== "good" && (
              <button onClick={() => void run("done")} disabled={busy} className={button}>
                <Check size={13} /> Done
              </button>
            )}
            {snoozing ? (
              <span className="flex items-center gap-1" role="group" aria-label="Snooze for">
                {SNOOZES.map((s) => (
                  <button key={s.days} onClick={() => void run("snoozed", s.days)} disabled={busy} className={button}>
                    {s.label}
                  </button>
                ))}
                <button onClick={() => setSnoozing(false)} className={button} aria-label="Cancel snooze">
                  <X size={13} />
                </button>
              </span>
            ) : (
              <button onClick={() => setSnoozing(true)} disabled={busy} className={button}>
                <AlarmClock size={13} /> Snooze
              </button>
            )}
            <button onClick={() => void run("dismissed")} disabled={busy} className={button} title="Don't show this again">
              <X size={13} /> {item.level === "good" ? "Hide" : "Not relevant"}
            </button>
          </div>
        </div>
      </div>
    </li>
  );
}

function SetAside({ data, onRestore }: { data: Advice; onRestore: (i: { key: string; title: string }) => void }) {
  return (
    <details className="mt-2 rounded-xl border border-edge bg-panel/60 text-sm">
      <summary className="cursor-pointer list-none px-4 py-2.5 text-slate-400 select-none hover:text-slate-200">
        {data.set_aside.length} set aside (done, snoozed or not relevant)
      </summary>
      <ul className="divide-y divide-edge border-t border-edge">
        {data.set_aside.map((i) => (
          <li key={i.key} className="flex items-center gap-3 px-4 py-2">
            <span className="min-w-0 flex-1 truncate text-slate-300">{i.title}</span>
            <span className="shrink-0 text-xs text-slate-500">
              {i.status === "snoozed" && i.until ? `until ${formatDate(i.until)}` : i.status === "done" ? "done" : "hidden"}
            </span>
            <button onClick={() => onRestore(i)} className="flex shrink-0 items-center gap-1 text-xs text-sky-300 hover:text-sky-200">
              <RotateCcw size={12} /> Bring back
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}

function AdviceGuide() {
  return (
    <Guide title="How advice works">
      <div>
        <GuideHeading>Where it comes from</GuideHeading>
        Only your own data: accounts, cards, subscriptions, transactions from bank alerts and statements, the Money health checks
        and your Organiser. Nothing leaves Nexus and no AI model reads it to make these; they're fixed rules you can see in "How
        Nexus knows".
      </div>
      <div>
        <GuideHeading>What it looks for</GuideHeading>
        Card bills due or overdue, cards used past 30% of their limit, cards about to expire, subscriptions spread across many cards
        or charged in foreign currency, price rises, double charges, unusual spending, bank fees and an Inbox that's piling up.
      </div>
      <div>
        <GuideHeading>Done, snooze, not relevant</GuideHeading>
        Done and Not relevant hide an item until something changes (a new month or a new charge makes a new item). Snooze brings it
        back after a day, a week or a month. Everything set aside is listed at the bottom so you can bring it back.
      </div>
      <div>
        <GuideHeading>Safety, not investing</GuideHeading>
        Advice is about avoiding late fees, fraud, surprise charges and a blocked card taking your subscriptions down. It never tells
        you what to invest in. Your phone gets one notice when something new needs doing, with a count and no amounts.
      </div>
    </Guide>
  );
}
