import { AlertTriangle, BellRing, Check, ExternalLink, HelpCircle, OctagonAlert, Plus, Repeat, Settings2, Undo2, X } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../../lib/api";
import { CADENCE_LABEL, dayLabel, isoToday, rupees, whenLabel, type Cadence, type Subscription, type SubscriptionSave } from "../../lib/money";
import { toast } from "../Feedback";
import { FormDialog, moneyText, read, type Field } from "./FormDialog";
import { Empty, Spinner, Stat, useMoney } from "./parts";

const CATEGORIES = ["Subscriptions", "Entertainment", "Bills and utilities", "Rent and housing", "Health", "Education", "Investments", "Other"];

async function save(body: SubscriptionSave, done: string) {
  try {
    await api.moneySaveSubscription(body);
    toast(done);
  } catch (e) {
    toast((e as Error).message, "error");
  }
}

/** Subscriptions and repeating bills: what's new to review, what you keep (with a reminder
 * before each renewal), what you've cancelled (and whether they charged anyway), and price
 * rises. */
export function SubscriptionsView() {
  const { data, error } = useMoney(api.moneySubscriptions);
  const [editing, setEditing] = useState<Subscription | "new" | null>(null);
  if (error) return <p className="text-sm text-rose-400">{error}</p>;
  if (!data) return <Spinner />;
  const live = data.items.filter((s) => s.status === "new" || s.status === "active");
  const review = data.items.filter((s) => s.status === "new");
  const keeping = live.filter((s) => s.status === "active");
  const cancelled = data.items.filter((s) => s.status === "cancelled");
  const ignored = data.items.filter((s) => s.status === "ignored");
  const alerts = data.items.filter((s) => s.charged_after_cancel || (s.price_change_paise > 0 && s.status !== "ignored"));

  return (
    <>
      <div className="mb-4 flex flex-wrap items-start gap-2">
        <p className="min-w-0 flex-1 text-sm text-slate-400">
          Found from payments that repeat, plus any you add. Tell Money which to keep and it reminds you before each renewal;
          mark one cancelled and it watches for a charge that shouldn't happen.
        </p>
        <button onClick={() => setEditing("new")} className="flex shrink-0 items-center gap-1 rounded-md border border-edge px-2.5 py-1.5 text-sm text-slate-200 hover:bg-edge/60">
          <Plus size={14} /> Add one
        </button>
      </div>
      <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Subscriptions" value={rupees(data.subscriptions_monthly_paise, { whole: true })} sub="a month" />
        <Stat label="Bills, rent and SIPs" value={rupees(data.bills_monthly_paise, { whole: true })} sub="a month" />
        <Stat label="All of it, a year" value={rupees(data.yearly_paise, { whole: true })} sub={`${live.length} payments`} />
        <Stat label="Saved by cancelling" value={rupees(data.saved_monthly_paise, { whole: true })} sub={`a month, ${cancelled.length} cancelled`} />
      </div>

      {alerts.map((s) => (
        <Alert key={`alert-${s.key}`} s={s} onOpen={() => setEditing(s)} />
      ))}

      {data.items.length === 0 && <Empty>Nothing repeating found yet. Payments show up here after three months of transactions, or add one yourself.</Empty>}

      {review.length > 0 && (
        <Group title={`New, to review (${review.length})`} hint="Keep it, mark it cancelled, or say it isn't a subscription.">
          {review.map((s) => (
            <Row key={s.key} s={s} onOpen={() => setEditing(s)}>
              <span className="flex flex-wrap gap-1.5">
                <Pill onClick={() => save({ merchant: s.merchant, status: "active" }, `Keeping ${s.merchant}`)} icon={Check}>
                  Keep
                </Pill>
                <Pill onClick={() => save({ merchant: s.merchant, status: "cancelled" }, `${s.merchant} marked cancelled`)} icon={X}>
                  Cancelled
                </Pill>
                <Pill onClick={() => save({ merchant: s.merchant, status: "ignored" }, `${s.merchant} hidden`)} icon={HelpCircle}>
                  Not a subscription
                </Pill>
              </span>
            </Row>
          ))}
        </Group>
      )}

      {(["subscription", "bill"] as const).map((group) => {
        const rows = keeping.filter((s) => s.group === group);
        if (!rows.length) return null;
        return (
          <Group key={group} title={group === "subscription" ? "Subscriptions you keep" : "Bills, rent and SIPs"}>
            {rows.map((s) => (
              <Row key={s.key} s={s} onOpen={() => setEditing(s)} />
            ))}
          </Group>
        );
      })}

      {cancelled.length > 0 && (
        <Group title="Cancelled" hint="Kept here so a charge after cancelling is caught.">
          {cancelled.map((s) => (
            <Row key={s.key} s={s} onOpen={() => setEditing(s)} muted />
          ))}
        </Group>
      )}

      {ignored.length > 0 && (
        <details className="mt-2 text-sm">
          <summary className="cursor-pointer text-xs text-slate-400">Not subscriptions ({ignored.length})</summary>
          <ul className="mt-2 divide-y divide-edge/60 rounded-xl border border-edge bg-panel">
            {ignored.map((s) => (
              <li key={s.key} className="flex items-center gap-2 px-3 py-2">
                <span className="min-w-0 flex-1 truncate text-slate-400">{s.merchant}</span>
                <button
                  onClick={async () => {
                    await api.moneyForgetSubscription(s.key);
                    toast(`${s.merchant} is back to review`);
                  }}
                  className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200"
                >
                  <Undo2 size={12} /> Undo
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}

      {editing && <SubscriptionDialog s={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function Group({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="mb-5">
      <h2 className="mb-1 text-sm font-medium text-slate-300">{title}</h2>
      {hint && <p className="mb-2 text-xs text-slate-500">{hint}</p>}
      <ul className="divide-y divide-edge/60 rounded-xl border border-edge bg-panel">{children}</ul>
    </section>
  );
}

function Pill({ onClick, icon: Icon, children }: { onClick: () => void; icon: typeof Check; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className="flex items-center gap-1 rounded-full border border-edge px-2.5 py-1 text-xs text-slate-200 hover:bg-edge/60">
      <Icon size={12} /> {children}
    </button>
  );
}

function Row({ s, onOpen, muted, children }: { s: Subscription; onOpen: () => void; muted?: boolean; children?: React.ReactNode }) {
  const navigate = useNavigate();
  const bits = [
    CADENCE_LABEL[s.cadence],
    s.status === "cancelled"
      ? s.cancelled_on
        ? `cancelled ${dayLabel(s.cancelled_on)}`
        : "cancelled"
      : s.next_on
        ? `next ${dayLabel(s.next_on)} (${whenLabel(s.next_on)})`
        : null,
    s.status === "active" && s.remind_days ? `reminder ${s.remind_days} day${s.remind_days === 1 ? "" : "s"} before` : null,
    s.manual ? "added by you" : s.count ? `paid ${s.count}×` : null,
  ].filter(Boolean);
  return (
    <li className="px-3 py-2.5">
      <div className="flex items-center gap-3">
        <Repeat size={15} className="shrink-0 text-slate-500" />
        <button
          onClick={() => !s.manual && navigate(`/money/transactions?${new URLSearchParams({ month: "all", q: s.merchant })}`)}
          className="min-w-0 flex-1 text-left"
          title={s.manual ? undefined : "See its payments"}
        >
          <span className={`block truncate text-sm ${muted ? "text-slate-400" : "text-slate-100"}`}>
            {s.merchant}
            {s.not_seen && <span className="ml-2 text-xs text-slate-500">no charge lately: cancelled already?</span>}
          </span>
          <span className="block truncate text-xs text-slate-500">{bits.join(" · ")}</span>
        </button>
        <span className="text-right">
          <span className={`block tabular-nums text-sm ${muted ? "text-slate-400 line-through decoration-slate-600" : ""}`}>{rupees(s.amount_paise, { whole: true })}</span>
          {s.cadence !== "monthly" && <span className="block text-xs text-slate-500">{rupees(s.monthly_paise, { whole: true })} a month</span>}
        </span>
        {s.manage_url && (
          <a href={s.manage_url} target="_blank" rel="noreferrer" className="hidden p-1 text-slate-400 hover:text-white sm:block" aria-label={`Manage ${s.merchant} on its website`} title="Manage or cancel on their website">
            <ExternalLink size={14} />
          </a>
        )}
        <button onClick={onOpen} className="p-1 text-slate-400 hover:text-white" aria-label={`Settings for ${s.merchant}`}>
          <Settings2 size={14} />
        </button>
      </div>
      {children && <div className="mt-2 pl-7">{children}</div>}
    </li>
  );
}

function Alert({ s, onOpen }: { s: Subscription; onOpen: () => void }) {
  const late = s.charged_after_cancel;
  const Icon = late ? OctagonAlert : AlertTriangle;
  const colour = late ? "#d03b3b" : "#fab219";
  return (
    <div className="mb-3 flex items-start gap-2.5 rounded-xl border border-edge bg-panel px-3 py-2.5 text-sm" style={{ borderColor: `${colour}66` }}>
      <Icon size={16} className="mt-0.5 shrink-0" style={{ color: colour }} aria-hidden />
      <span className="min-w-0 flex-1 text-slate-300">
        {late ? (
          <>
            <b className="text-slate-100">{s.merchant} charged after you cancelled:</b> {rupees(late.amount_paise)} on {dayLabel(late.on)}. Ask
            them for a refund
            {s.manage_url ? (
              <>
                {" "}
                at{" "}
                <a href={s.manage_url} target="_blank" rel="noreferrer" className="text-sky-300 hover:text-sky-200">
                  their account page
                </a>
              </>
            ) : null}
            .
          </>
        ) : (
          <>
            <b className="text-slate-100">{s.merchant} went up</b> from {rupees(s.previous_paise ?? 0, { whole: true })} to {rupees(s.amount_paise, { whole: true })}
            {`, ${rupees(Math.abs(s.price_change_paise) * (12 / ({ monthly: 1, quarterly: 3, yearly: 12 } as const)[s.cadence]), { whole: true })}\u00a0more a year.`}
          </>
        )}
      </span>
      <button onClick={onOpen} className="shrink-0 text-xs text-sky-300 hover:text-sky-200">
        Review
      </button>
    </div>
  );
}

function SubscriptionDialog({ s, onClose }: { s: Subscription | null; onClose: () => void }) {
  const manual = !s || s.manual;
  const fields: Field[] = [
    ...(s ? [] : [{ key: "merchant", label: "Name", kind: "text", value: "", placeholder: "Gym, Netflix, cloud storage…" } as Field]),
    {
      key: "status",
      label: "Status",
      kind: "select",
      value: s && s.status !== "new" ? s.status : "active",
      options: [
        ["active", "Keeping it"],
        ["cancelled", "I've cancelled it"],
        ["ignored", "Not a subscription"],
      ],
    },
    ...(manual
      ? ([
          { key: "amount", label: "Amount", kind: "money", value: moneyText(s?.amount_paise), half: true },
          {
            key: "cadence",
            label: "How often",
            kind: "select",
            value: s?.cadence ?? "monthly",
            options: Object.entries(CADENCE_LABEL) as [string, string][],
            half: true,
          },
          { key: "next_on", label: "Next charge", kind: "date", value: s?.next_on ?? isoToday(), half: true },
          { key: "category", label: "Kind", kind: "select", value: s?.category ?? "Subscriptions", options: CATEGORIES.map((c) => [c, c]), half: true },
        ] as Field[])
      : []),
    {
      key: "remind_days",
      label: "Remind me before each renewal",
      kind: "select",
      value: String(s?.remind_days ?? 3),
      options: [
        ["0", "No reminder"],
        ["1", "1 day before"],
        ["3", "3 days before"],
        ["7", "A week before"],
        ["14", "Two weeks before"],
      ],
      when: (v) => v.status === "active",
    },
    { key: "cancelled_on", label: "Cancelled on", kind: "date", value: s?.cancelled_on ?? isoToday(), when: (v) => v.status === "cancelled", hint: "A charge after this day is flagged." },
    { key: "manage_url", label: "Manage or cancel at", kind: "url", value: s?.manage_url ?? "", optional: true, placeholder: "https://…" },
    { key: "note", label: "Note", kind: "text", value: s?.note ?? "", optional: true, placeholder: "Shared with family, annual plan cheaper…" },
  ];
  return (
    <FormDialog
      title={s ? s.merchant : "Add a subscription or bill"}
      description={
        s && !s.manual ? (
          <span className="flex items-center gap-1.5">
            <BellRing size={13} /> Found from {s.count} payments. Amount and dates follow the payments.
          </span>
        ) : (
          "For one that doesn't show in your statements: paid by UPI autopay to a person, in cash, or by another card."
        )
      }
      fields={fields}
      submitLabel="Save"
      onClose={onClose}
      extra={
        s && (s.manual || s.status !== "new") ? (
          <button
            type="button"
            onClick={async () => {
              await api.moneyForgetSubscription(s.key);
              toast(s.manual ? "Removed" : "Back to review");
              onClose();
            }}
            className="text-xs text-slate-400 hover:text-rose-300"
          >
            {s.manual ? "Remove it" : "Forget my choices for this one"}
          </button>
        ) : undefined
      }
      onSubmit={async (v) => {
        const body: SubscriptionSave = {
          merchant: s?.merchant ?? v.merchant.trim(),
          status: v.status as SubscriptionSave["status"],
          remind_days: v.status === "active" ? Number(v.remind_days) : s?.remind_days ?? 3,
          manage_url: v.manage_url.trim() || null,
          note: v.note.trim() || null,
        };
        if (v.status === "cancelled") Object.assign(body, { cancelled_on: v.cancelled_on });
        if (manual) Object.assign(body, { manual: true, amount_paise: read.money(v.amount)!, cadence: v.cadence as Cadence, next_on: v.next_on, category: v.category });
        await api.moneySaveSubscription(body);
        toast("Saved");
      }}
    />
  );
}
