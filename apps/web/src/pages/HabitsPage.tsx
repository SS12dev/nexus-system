import { Bike, Cigarette, Flame, Lock, ShoppingBasket, Wallet } from "lucide-react";
import { useSearchParams } from "react-router";
import { Guide, GuideHeading } from "../components/Guide";
import { DayByDay } from "../components/money/Daily";
import { MonthPicker, Spinner, useMoney } from "../components/money/parts";
import { OrdersCatchUp } from "../components/money/Suggestions";
import { api } from "../lib/api";
import { rupees, thisMonth, type Streaks } from "../lib/money";
import { isOwner } from "../lib/types";
import { useStore } from "../store/chat";

/** Habits: everyday life in numbers, kept apart from Money's accounts and bills. The streaks to
 * keep going, what each day cost, the leaks under it, what was in your orders, and cigarettes.
 * Built from Money's transactions; more habits can join later. Owner only, like Money. */
export function HabitsPage() {
  const user = useStore((s) => s.user)!;
  const latest = thisMonth();
  const [params, setParams] = useSearchParams();
  const month = params.get("month") ?? latest;
  if (!isOwner(user)) {
    return (
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-xl px-4 py-10 text-center text-sm text-slate-400">
          <Lock className="mx-auto mb-2 text-slate-500" size={20} />
          Habits is only for the owner of this Nexus.
        </div>
      </div>
    );
  }
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 py-5 sm:py-6">
        <div className="mb-1 flex items-center gap-3">
          <h1 className="text-xl font-semibold">Habits</h1>
          <span className="flex items-center gap-1 rounded-full border border-edge px-2 py-0.5 text-xs text-slate-400" title="Only you can see this">
            <Lock size={11} /> Private
          </span>
        </div>
        <p className="mb-3 text-sm text-slate-400">
          Your everyday life in numbers: the runs to keep going, what each day cost, where money slips away, and cigarettes. Built from
          the same transactions as Money.
        </p>
        <Guide title="How Habits works">
          <div>
            <GuideHeading>Streaks</GuideHeading>
            Runs counted up to today. "Under your usual day" counts days in a row when everyday spending (without rent, EMIs, bills and
            subscriptions) stayed at or under your average day over the last three months; a day over it starts the count again. The
            others count days since the last food delivery, quick-commerce order and cigarettes.
          </div>
          <div>
            <GuideHeading>Day by day</GuideHeading>
            The calendar shows each day's everyday spending, so habits aren't hidden by the big fixed payments. Tap a day for its
            spends. Habits and leaks shows food deliveries, quick commerce and what was in them, small spends, and shops you go back to;
            a tip appears only once the numbers behind it are in.
          </div>
          <div>
            <GuideHeading>What did you buy?</GuideHeading>
            Zomato's emails list what you ordered, so those are read for you. Zepto, Blinkit and Instamart send none, so Money asks.
            It catches up once on this month's orders, then asks only about each new one. Screenshots are quickest: pick several at
            once (one per order, showing the items and the total). The model on your own computer reads each one, Money finds its
            payment by the total and date, and you check the list before it's saved. The images aren't kept.
          </div>
          <div>
            <GuideHeading>Cigarettes</GuideHeading>
            Paan-shop payments and cigarettes inside orders are counted. The pack size is read from the item (10s, 20s), and each new
            pack is taken as the last one finished, which gives how many a day, how long a pack lasts, and when the next is due. Set a
            monthly limit on the card to cut down to; it's also a budget in Money.
          </div>
        </Guide>
        <StreakRow />
        <OrdersCatchUp />
        <div className="mb-4">
          <MonthPicker month={month} latest={latest} onChange={(m) => setParams(m === latest ? {} : { month: m }, { replace: true })} />
        </div>
        <DayByDay month={month} />
      </div>
    </div>
  );
}

function StreakRow() {
  const { data } = useMoney(() => api.moneyDaily(thisMonth()), []);
  if (!data) return <Spinner />;
  const s: Streaks = data.streaks;
  if (!s.tracked_days) return null;
  const since = (n: number | null) => (n == null ? "90+ days" : n === 0 ? "Today" : `${n} day${n === 1 ? "" : "s"}`);
  return (
    <section aria-label="Streaks" className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
      <Tile
        icon={Wallet}
        label="Under your usual day"
        value={`${s.under} day${s.under === 1 ? "" : "s"}`}
        sub={`Usual ${rupees(s.usual_paise, { whole: true })} · best this month ${s.under_best}`}
        lit={s.under >= 3}
      />
      <Tile icon={Bike} label="Since a food delivery" value={since(s.delivery)} lit={(s.delivery ?? 99) >= 3} />
      <Tile icon={ShoppingBasket} label="Since a quick-commerce order" value={since(s.basket)} lit={(s.basket ?? 99) >= 3} />
      <Tile icon={Cigarette} label="Since cigarettes" value={since(s.smoke)} lit={(s.smoke ?? 99) >= 1} />
    </section>
  );
}

/** One streak. A flame shows once a run is worth keeping. */
function Tile({ icon: Icon, label, value, sub, lit }: { icon: typeof Wallet; label: string; value: string; sub?: string; lit: boolean }) {
  return (
    <div className="min-w-0 rounded-xl border border-edge bg-panel p-3">
      <p className="flex items-center gap-1.5 text-xs text-slate-400">
        <Icon size={13} aria-hidden /> <span className="truncate">{label}</span>
      </p>
      <p className="mt-1 flex items-center gap-1.5 text-lg font-semibold tabular-nums text-slate-100">
        {value}
        {lit && <Flame size={16} className="text-amber-400" aria-label="On a streak" />}
      </p>
      {sub && <p className="text-xs leading-tight text-slate-500">{sub}</p>}
    </div>
  );
}
