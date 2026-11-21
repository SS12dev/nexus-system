import { CalendarClock, CreditCard, FileUp, HandCoins, LayoutDashboard, Landmark, LineChart, ListOrdered, Lock, Mail, PiggyBank, Repeat, Settings2, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate, useSearchParams } from "react-router";
import { toast } from "../components/Feedback";
import { Guide, GuideHeading } from "../components/Guide";
import { BudgetMeter, CategoryBars, TrendBars } from "../components/money/Charts";
import { Card, Empty, MonthPicker, Spinner, Stat, useMoney, useMoneySettings } from "../components/money/parts";
import { AccountsView } from "../components/money/Accounts";
import { HealthStrip, HealthView } from "../components/money/Health";
import { PlanView } from "../components/money/Plan";
import { SavingsView } from "../components/money/Savings";
import { SetupView } from "../components/money/Setup";
import { SubscriptionsView } from "../components/money/Subscriptions";
import { TransactionsView } from "../components/money/Transactions";
import { Select } from "../components/Select";
import { api } from "../lib/api";
import { formatAgo } from "../lib/format";
import { dayLabel, monthLabel, parseRupees, rupees, thisMonth } from "../lib/money";
import { isOwner } from "../lib/types";
import { useStore } from "../store/chat";

/** Money: a read-only view of the owner's spending, budgets, repeating payments and mutual
 * funds, from alert emails, statements and CAS files. */
export function MoneyPage() {
  const user = useStore((s) => s.user)!;
  const latest = thisMonth();
  if (!isOwner(user)) {
    return (
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-xl px-4 py-10 text-center text-sm text-slate-400">
          <Lock className="mx-auto mb-2 text-slate-500" size={20} />
          Money is only for the owner of this Nexus.
        </div>
      </div>
    );
  }
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 py-5 sm:py-6">
        <Header />
        <Routes>
          <Route index element={<OverviewView latest={latest} />} />
          <Route path="transactions" element={<TransactionsView latest={latest} />} />
          <Route path="budgets" element={<BudgetsView latest={latest} />} />
          <Route path="health" element={<HealthView />} />
          <Route path="accounts" element={<AccountsView />} />
          <Route path="subscriptions" element={<SubscriptionsView />} />
          <Route path="savings" element={<SavingsView />} />
          <Route path="investments" element={<Navigate to="/money/savings" replace />} />
          <Route path="plan" element={<PlanView />} />
          <Route path="setup" element={<SetupView />} />
          <Route path="*" element={<Navigate to="/money" replace />} />
        </Routes>
      </div>
    </div>
  );
}

const TABS = [
  { to: "/money", label: "Overview", icon: LayoutDashboard },
  { to: "/money/health", label: "Health", icon: ShieldCheck },
  { to: "/money/accounts", label: "Accounts", icon: CreditCard },
  { to: "/money/transactions", label: "Transactions", icon: ListOrdered },
  { to: "/money/budgets", label: "Budgets", icon: PiggyBank },
  { to: "/money/subscriptions", label: "Subscriptions", icon: Repeat },
  { to: "/money/savings", label: "Savings", icon: Landmark },
  { to: "/money/plan", label: "Plan", icon: LineChart },
  { to: "/money/setup", label: "Import and settings", icon: Settings2 },
];

function Header() {
  const path = useLocation().pathname.replace(/\/$/, "");
  const home = path === "/money";
  const nav = useRef<HTMLElement>(null);
  useEffect(() => {
    // On a phone the tab row scrolls: keep the open tab in view.
    nav.current?.querySelector<HTMLElement>('[aria-current="page"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [path]);
  return (
    <>
      <div className="mb-1 flex items-center gap-3">
        <h1 className="text-xl font-semibold">Money</h1>
        <span className="flex items-center gap-1 rounded-full border border-edge px-2 py-0.5 text-xs text-slate-400" title="Only you can see this, and nothing here can move money">
          <Lock size={11} /> Private, read-only
        </span>
      </div>
      <p className="mb-3 text-sm text-slate-400">
        Every account, card, loan and saving in one place: whether you're safe, what you can spend, what's due, and where
        you're heading. Built from your own emails and statements.
      </p>
      {home && (
        <Guide title="How Money works">
          <div>
            <GuideHeading>Where the numbers come from</GuideHeading>
            Bank and card <b className="text-slate-300">alert emails</b> in your Gmail (checked every hour, once switched on),{" "}
            <b className="text-slate-300">statements</b> you download from net banking and upload, and anything you add by
            hand. Mutual funds come from a CAMS or KFintech <b className="text-slate-300">CAS</b> PDF.
          </div>
          <div>
            <GuideHeading>What counts as spending</GuideHeading>
            Money out, minus transfers between your own accounts (like paying your credit card bill, which would otherwise
            count twice). Open any transaction to leave it out of totals, say a refund that cancels a purchase.
          </div>
          <div>
            <GuideHeading>Categories learn from you</GuideHeading>
            Each transaction gets a category from its merchant (Swiggy is Food and dining). Change one and tick “Always for
            this merchant”: the rest of that merchant's transactions, and future ones, follow.
          </div>
          <div>
            <GuideHeading>Accounts, cards and loans</GuideHeading>
            Balances come from the latest alert or statement that shows one. Card bills and due dates are read from statement
            emails, with a reminder three days before. Card EMIs are picked up from the bank's “converted to EMI” email and
            the EMI lines; the purchase itself is then left out so it isn't counted twice.
          </div>
          <div>
            <GuideHeading>Subscriptions</GuideHeading>
            Payments that repeat are found for you. Keep, cancel or hide each one: kept ones get a reminder before renewal,
            and a charge after you cancel or a price rise is flagged.
          </div>
          <div>
            <GuideHeading>Health: are you safe?</GuideHeading>
            The Health tab checks the things that keep you safe: an emergency fund, how much you keep each month, EMIs and
            card balances, bills covered and insurance. It also says how much is safe to spend for the rest of the month
            after your saving target.
          </div>
          <div>
            <GuideHeading>Savings and the plan</GuideHeading>
            Add FDs, RDs, PPF, EPF and NPS once and they're valued from their terms. The Plan tab adds it all up into net
            worth, forecasts it, and shows what a goal or a monthly SIP would take.
          </div>
          <div>
            <GuideHeading>Private by design</GuideHeading>
            Everything stays on your server. No bank login and no AI model reads your statements; bank wording is encrypted,
            and only you can open this page. Your assistant can read Money (never the bank's wording or statements) unless
            you switch it off.
          </div>
          <div>
            <GuideHeading>Getting started</GuideHeading>
            Open <Link to="/money/setup" className="text-sky-300 hover:text-sky-200">Import and settings</Link>: upload a
            recent statement for history, then switch on alert emails to stay current.
          </div>
        </Guide>
      )}
      <nav ref={nav} className="-mx-4 mb-5 flex gap-1 overflow-x-auto px-4 pb-1 [scrollbar-width:none]" aria-label="Money">
        {TABS.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            end={t.to === "/money"}
            className={({ isActive }) =>
              `flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm ${
                isActive ? "border-sky-500/60 bg-sky-500/15 text-sky-100" : "border-edge text-slate-300 hover:bg-edge/60"
              }`
            }
          >
            <t.icon size={14} />
            {t.label}
          </NavLink>
        ))}
      </nav>
    </>
  );
}

// --- Overview -----------------------------------------------------------------------------------

function OverviewView({ latest }: { latest: string }) {
  const [params, setParams] = useSearchParams();
  const month = params.get("month") ?? latest;
  const navigate = useNavigate();
  const { data, error } = useMoney(() => api.moneyOverview(month), [month]);
  const pick = (m: string) => setParams(m === latest ? {} : { month: m }, { replace: true });
  const txns = (extra: Record<string, string>) => navigate(`/money/transactions?${new URLSearchParams({ month, ...extra })}`);

  if (error) return <p className="text-sm text-rose-400">{error}</p>;
  if (!data) return <Spinner />;
  if (!data.has_data) return <Welcome />;
  const gain = data.funds_cost_paise ? data.funds_value_paise - data.funds_cost_paise : null;
  return (
    <>
      {month === latest && <HealthStrip />}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <MonthPicker month={month} latest={latest} onChange={pick} />
        <SyncLine on={data.gmail_sync} at={data.gmail_synced_at} />
      </div>
      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat
          label="Spent"
          value={rupees(data.spent_paise, { whole: true })}
          sub={data.invested_paise ? `plus ${rupees(data.invested_paise, { whole: true })} invested` : `${data.count} transactions`}
          title="Money out, without transfers between your own accounts or money put into investments"
        />
        <Stat
          label="Came in"
          value={rupees(data.income_paise, { whole: true })}
          sub={data.net_paise >= 0 ? `${rupees(data.net_paise, { whole: true })} left over` : `${rupees(-data.net_paise, { whole: true })} more out than in`}
          title="Left over: what came in, less spending and investing"
        />
        <Stat
          label="Subscriptions and bills"
          value={rupees(data.subscriptions_monthly_paise, { whole: true })}
          sub={data.subscriptions_to_review ? `a month · ${data.subscriptions_to_review} new to review` : "a month"}
        />
        <Stat
          label="Mutual funds"
          value={data.funds_value_paise ? rupees(data.funds_value_paise, { whole: true }) : "—"}
          sub={
            gain != null
              ? `${rupees(gain, { whole: true, sign: true })} (${gain >= 0 ? "+" : "−"}${Math.abs((gain / data.funds_cost_paise) * 100).toFixed(1)}%)`
              : data.funds_value_paise
                ? ""
                : "Add a CAS to see them"
          }
          title={data.funds_cost_paise ? `${rupees(data.funds_cost_paise, { whole: true })} put in` : undefined}
        />
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="grid min-w-0 content-start gap-4">
          <Card title={`Where it went in ${monthLabel(month)}`}>
            {data.categories.length ? (
              <CategoryBars categories={data.categories} total={data.spent_paise} onPick={(category) => txns({ category })} />
            ) : (
              <p className="text-sm text-slate-500">No spending this month yet.</p>
            )}
          </Card>
          <Card title="Biggest merchants">
            {data.merchants.length ? (
              <ol className="space-y-1 text-sm">
                {data.merchants.slice(0, 6).map((m) => (
                  <li key={m.merchant}>
                    <button onClick={() => txns({ q: m.merchant })} className="flex w-full items-center gap-2 rounded px-1 py-1 text-left hover:bg-edge/50">
                      <span className="min-w-0 flex-1 truncate text-slate-200">{m.merchant}</span>
                      <span className="text-xs text-slate-500">{m.count}×</span>
                      <span className="w-24 text-right tabular-nums text-slate-200">{rupees(m.spent_paise, { whole: true })}</span>
                    </button>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-sm text-slate-500">Nothing yet.</p>
            )}
          </Card>
        </div>
        <div className="grid min-w-0 content-start gap-4">
          <Card>
            <TrendBars trend={data.trend} month={month} onPick={pick} />
          </Card>
          <Card
            title="Budgets"
            action={
              <Link to="/money/budgets" className="text-sky-300 hover:text-sky-200">
                {data.budgets.length ? "Edit" : "Set one"}
              </Link>
            }
          >
            {data.budgets.length ? (
              <div className="space-y-3">
                {data.budgets.map((b) => (
                  <BudgetMeter key={b.category} budget={b} progress={month === latest ? data.month_progress : 1} compactRow />
                ))}
              </div>
            ) : (
              <p className="text-sm text-slate-500">A monthly limit for a category shows here, with whether you're on pace.</p>
            )}
          </Card>
          <Card
            title="Due in the next two weeks"
            action={
              <Link to="/money/accounts" className="text-sky-300 hover:text-sky-200">
                Accounts
              </Link>
            }
          >
            {data.upcoming.length ? (
              <ul className="space-y-1.5 text-sm">
                {data.upcoming.map((u) => {
                  const Icon = u.kind === "card" ? CreditCard : u.kind === "emi" ? HandCoins : Repeat;
                  return (
                    <li key={`${u.kind}-${u.label}-${u.on}`} className="flex items-center gap-2">
                      <Icon size={14} className="shrink-0 text-slate-500" />
                      <span className="min-w-0 flex-1 truncate text-slate-200">
                        {u.label}
                        {u.kind === "emi" && <span className="ml-1.5 text-xs text-slate-500">{u.detail}</span>}
                      </span>
                      <span className={`text-xs ${u.detail === "overdue" ? "text-rose-300" : "text-slate-500"}`}>{dayLabel(u.on)}</span>
                      <span className="w-20 text-right tabular-nums">{rupees(u.amount_paise, { whole: true })}</span>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="flex items-center gap-2 text-sm text-slate-500">
                <CalendarClock size={14} /> Nothing due: no card bills, EMIs or renewals in the next two weeks.
              </p>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}

function SyncLine({ on, at }: { on: boolean; at: string | null }) {
  return (
    <Link to="/money/setup" className="ml-auto flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200">
      <Mail size={13} />
      {on ? (at ? `Alert emails checked ${formatAgo(at)}` : "Reading alert emails…") : "Alert emails off"}
    </Link>
  );
}

function Welcome() {
  const { data: settings } = useMoneySettings();
  return (
    <div className="mx-auto max-w-2xl rounded-xl border border-edge bg-panel p-5">
      <h2 className="mb-1 text-lg font-semibold">Let's get your numbers in</h2>
      <p className="mb-4 text-sm text-slate-400">Two minutes, no bank login. Do either or both; they're matched so nothing counts twice.</p>
      <ol className="space-y-3 text-sm">
        <li className="flex gap-3">
          <FileUp size={18} className="mt-0.5 shrink-0 text-sky-300" />
          <span>
            <b className="text-slate-200">Upload a statement</b> for history: download the last few months from net banking as
            CSV or Excel and add it in{" "}
            <Link to="/money/setup" className="text-sky-300 hover:text-sky-200">
              Import and settings
            </Link>
            .
          </span>
        </li>
        <li className="flex gap-3">
          <Mail size={18} className="mt-0.5 shrink-0 text-sky-300" />
          <span>
            <b className="text-slate-200">Switch on alert emails</b> to stay current: the debit and credit alerts your bank
            already emails you are read every hour{settings && !settings.gmail_connected ? " (allow Gmail in Settings first)" : ""}.
          </span>
        </li>
        <li className="flex gap-3">
          <Landmark size={18} className="mt-0.5 shrink-0 text-sky-300" />
          <span>
            <b className="text-slate-200">Add your mutual funds</b> with a CAS PDF from CAMS; values update every night.
          </span>
        </li>
      </ol>
      <Link to="/money/setup" className="mt-5 inline-flex rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink hover:bg-sky-400">
        Open Import and settings
      </Link>
    </div>
  );
}

// --- Budgets ------------------------------------------------------------------------------------

function BudgetsView({ latest }: { latest: string }) {
  const { data } = useMoney(() => api.moneyOverview(latest), []);
  const categories = useMoneySettings().data?.categories ?? [];
  const [category, setCategory] = useState("");
  const [amount, setAmount] = useState("");
  if (!data) return <Spinner />;
  const budgeted = new Set(data.budgets.map((b) => b.category));
  const spendable = categories.filter((c) => !["Income", "Transfers", "Investments"].includes(c) && !budgeted.has(c));
  const unbudgeted = data.categories.filter((c) => !budgeted.has(c.category) && c.category !== "Transfers");
  const save = async (cat: string, text: string) => {
    const p = parseRupees(text);
    if (!cat || !p) {
      toast("Choose a category and type an amount in rupees", "error");
      return;
    }
    try {
      await api.moneySetBudget(cat, p);
      toast(`${cat}: ${rupees(p, { whole: true })} a month`);
      setCategory("");
      setAmount("");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <>
      <p className="mb-4 text-sm text-slate-400">
        A monthly limit per category, for {monthLabel(latest)}. The tick shows how far through the month we are: spending past
        it means you're ahead of pace.
      </p>
      <form
        className="mb-5 flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void save(category, amount);
        }}
      >
        <Select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category" className="min-w-48">
          <option value="">Choose a category</option>
          {spendable.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </Select>
        <input
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          inputMode="decimal"
          placeholder="₹ a month"
          aria-label="Monthly limit in rupees"
          className="w-32 rounded-md border border-edge bg-ink px-2.5 py-1.5 text-base sm:text-sm"
        />
        <button type="submit" className="rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink hover:bg-sky-400">
          Set budget
        </button>
      </form>
      {data.budgets.length ? (
        <div className="grid gap-3 md:grid-cols-2">
          {data.budgets.map((b) => (
            <div key={b.category} className="relative">
              <BudgetMeter budget={b} progress={data.month_progress} />
              <div className="absolute right-3 bottom-2.5 flex gap-2 text-xs">
                <button
                  onClick={() => {
                    setCategory(b.category);
                    setAmount(String(Math.round(b.monthly_paise / 100)));
                  }}
                  className="text-slate-400 hover:text-slate-200"
                >
                  Change
                </button>
                <button
                  onClick={async () => {
                    try {
                      await api.moneyDeleteBudget(b.category);
                      toast("Budget removed");
                    } catch (e) {
                      toast((e as Error).message, "error");
                    }
                  }}
                  className="text-slate-400 hover:text-rose-300"
                  aria-label={`Remove the ${b.category} budget`}
                >
                  <Trash2 size={12} />
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <Empty>No budgets yet. Pick a category you'd like to keep an eye on.</Empty>
      )}
      {unbudgeted.length > 0 && (
        <Card title="Spending without a budget this month" className="mt-5">
          <ul className="space-y-1 text-sm">
            {unbudgeted.map((c) => (
              <li key={c.category} className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-slate-300">{c.category}</span>
                <span className="tabular-nums text-slate-400">{rupees(c.spent_paise, { whole: true })}</span>
                {!["Income", "Investments"].includes(c.category) && (
                  <button onClick={() => setCategory(c.category)} className="text-xs text-sky-300 hover:text-sky-200">
                    Set a budget
                  </button>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
