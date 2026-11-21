import { CheckCircle2, CreditCard, History, ImagePlus, Landmark, Loader2, Mail, ShieldCheck, Smartphone, XCircle } from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router";
import { api } from "../../lib/api";
import { dayLabel, mergeScreenshotRows, rupees, type MoneySettings, type ScreenshotReading, type ScreenshotRow } from "../../lib/money";
import { toast } from "../Feedback";
import { Select } from "../Select";
import { FormDialog, read, type Field } from "./FormDialog";
import { Card, useAccounts, useMoney } from "./parts";

// --- Adding an account by hand --------------------------------------------------------------------

/** Type in a bank account or card so alert emails with the same last four digits land on it,
 * with its balance or limit from the app. */
export function AddAccountDialog({ onClose }: { onClose: () => void }) {
  const { data } = useMoney(api.moneyBanks);
  if (!data) return null;
  const fields: Field[] = [
    {
      key: "kind",
      label: "Type",
      kind: "select",
      value: "bank",
      options: [
        ["bank", "Bank account"],
        ["card", "Credit card"],
      ],
      half: true,
    },
    { key: "mask", label: "Last four digits", kind: "text", value: "", half: true, placeholder: "1234", hint: "As shown in the app or on the card." },
    {
      key: "bank",
      label: "Bank",
      kind: "select",
      value: data.banks[0] ?? "",
      options: [...data.banks.map((b) => [b, b] as [string, string]), ["other", "Another bank…"]],
      half: true,
    },
    { key: "other", label: "Bank name", kind: "text", value: "", half: true, when: (v) => v.bank === "other" },
    { key: "name", label: "Name (optional)", kind: "text", value: "", optional: true, placeholder: "Salary account, Travel card…" },
    { key: "balance", label: "Balance now", kind: "money", value: "", optional: true, when: (v) => v.kind === "bank", hint: "From the app's home screen. Alerts keep it current after." },
    { key: "limit", label: "Credit limit", kind: "money", value: "", optional: true, when: (v) => v.kind === "card" },
  ];
  return (
    <FormDialog
      title="Add an account"
      description="For each account or card you use. Alert emails and screenshots with the same bank and last four digits then land on it."
      fields={fields}
      submitLabel="Add"
      onClose={onClose}
      onSubmit={async (v) => {
        const mask = v.mask.replace(/\D/g, "");
        if (mask.length !== 4) throw new Error("Type the last four digits of the account or card number.");
        const institution = v.bank === "other" ? v.other.trim() : v.bank;
        if (!institution) throw new Error("Type the bank's name.");
        const r = await api.moneyAddAccount({
          kind: v.kind as "bank" | "card",
          institution,
          mask,
          name: v.name.trim(),
          balance_paise: v.kind === "bank" ? read.money(v.balance) : null,
          credit_limit_paise: v.kind === "card" ? read.money(v.limit) : null,
        });
        toast(`Added ${r.label}`);
      }}
    />
  );
}

// --- Screenshots -----------------------------------------------------------------------------------

type Picked = ScreenshotRow & { keep: boolean; id: number };
type Failure = { name: string; error: string };

/** Screenshots of a banking, UPI or card app, read by the model on this computer. Everything is
 * shown for review first; nothing is saved until "Add". */
export function ScreenshotImport() {
  const input = useRef<HTMLInputElement>(null);
  const accounts = useAccounts();
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [readings, setReadings] = useState<ScreenshotReading[]>([]);
  const [rows, setRows] = useState<Picked[]>([]);
  const [failures, setFailures] = useState<Failure[]>([]);
  const [target, setTarget] = useState("");
  const [bank, setBank] = useState("");
  const [mask, setMask] = useState("");
  const [kind, setKind] = useState<"bank" | "card">("bank");
  const [keepBalance, setKeepBalance] = useState(true);
  const [busy, setBusy] = useState(false);

  const reset = () => {
    setReadings([]);
    setRows([]);
    setFailures([]);
    setTarget("");
    setBank("");
    setMask("");
    if (input.current) input.current.value = "";
  };

  const readAll = async (files: File[]) => {
    reset();
    const got: ScreenshotReading[] = [];
    const failed: Failure[] = [];
    setProgress({ done: 0, total: files.length });
    for (const [i, f] of files.entries()) {
      try {
        got.push(await api.moneyReadScreenshot(f));
      } catch (e) {
        const error = (e as Error).message;
        failed.push({ name: f.name, error });
        // The model isn't there at all: the rest would fail the same way.
        if (/didn't answer|can't read images|doesn't have/.test(error)) break;
      }
      setProgress({ done: i + 1, total: files.length });
    }
    setProgress(null);
    setFailures(failed);
    setReadings(got);
    const merged = mergeScreenshotRows(got.map((r) => r.rows));
    setRows(merged.map((r, id) => ({ ...r, id, keep: !r.seen })));
    const first = got.find((r) => r.account_id) ?? got.find((r) => r.mask) ?? got[0];
    if (first) {
      setTarget(first.account_id ?? (first.mask ? "new" : ""));
      setBank(first.bank ?? "");
      setMask(first.mask ?? "");
      setKind(first.kind);
    }
  };

  const balance = readings.find((r) => r.balance_paise != null)?.balance_paise ?? null;
  const card = readings.find((r) => r.card_limit_paise != null || r.card_due_paise != null) ?? null;
  const kept = rows.filter((r) => r.keep);
  const accountKind = target && target !== "new" ? (accounts.find((a) => a.id === target)?.kind ?? kind) : kind;

  const save = async () => {
    if (target === "new" && mask.replace(/\D/g, "").length !== 4) {
      toast("Type the account's last four digits, or pick an account.", "error");
      return;
    }
    setBusy(true);
    try {
      const r = await api.moneySaveScreenshots({
        account_id: target && target !== "new" ? target : null,
        institution: target === "new" ? bank : null,
        mask: target === "new" ? mask : null,
        kind: accountKind as "bank" | "card",
        balance_paise: keepBalance ? balance : null,
        card_limit_paise: keepBalance ? (card?.card_limit_paise ?? null) : null,
        card_due_paise: keepBalance ? (card?.card_due_paise ?? null) : null,
        card_due_on: keepBalance ? (card?.card_due_on ?? null) : null,
        rows: kept.map(({ occurred_on, merchant, amount_paise, direction }) => ({ occurred_on, merchant, amount_paise, direction })),
        images: Math.max(readings.length, 1),
      });
      toast(`Added ${r.added} transaction${r.added === 1 ? "" : "s"}${r.matched + r.skipped ? `; ${r.matched + r.skipped} were already in Money` : ""}${r.account_label ? ` to ${r.account_label}` : ""}`);
      reset();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const reviewing = readings.length > 0;
  return (
    <Card title="Screenshots from your banking apps">
      <p className="text-sm text-slate-400">
        Take screenshots in your bank, card or UPI app: the transaction list (scroll and take a few), the balance, or the card
        summary. One account at a time. The model on your own computer reads them; nothing goes to a cloud service, the
        images aren't kept, and you check every row before it's added.
      </p>
      <input
        ref={input}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        multiple
        className="hidden"
        onChange={(e) => {
          const files = [...(e.target.files ?? [])].slice(0, 20);
          if (files.length) void readAll(files);
        }}
      />
      {!reviewing && !progress && (
        <button
          onClick={() => input.current?.click()}
          className="mt-3 flex items-center gap-2 rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink hover:bg-sky-400"
        >
          <ImagePlus size={15} /> Choose screenshots
        </button>
      )}
      {progress && (
        <p className="mt-3 flex items-center gap-2 text-sm text-slate-300" role="status">
          <Loader2 size={14} className="animate-spin" />
          Reading {Math.min(progress.done + 1, progress.total)} of {progress.total}… about 10 to 30 seconds each on the Mac.
        </p>
      )}
      {failures.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs">
          {failures.map((f) => (
            <li key={f.name} className="flex items-start gap-1.5 text-slate-300">
              <XCircle size={13} className="mt-0.5 shrink-0" style={{ color: "#d03b3b" }} aria-hidden />
              <span>
                <span className="text-slate-400">{f.name}:</span> {f.error}
              </span>
            </li>
          ))}
        </ul>
      )}
      {reviewing && (
        <div className="mt-4 space-y-3">
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="block text-sm text-slate-300">
              <span className="mb-1 block">Add to</span>
              <Select value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Account" className="w-full">
                {!target && <option value="">Pick an account…</option>}
                <option value="new">{mask ? `New: ${bank || "Bank"} ••${mask}` : "A new account…"}</option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.label}
                  </option>
                ))}
              </Select>
            </label>
            {target === "new" && (
              <div className="grid grid-cols-[1fr_6rem] gap-2">
                <label className="block text-sm text-slate-300">
                  <span className="mb-1 block">Bank</span>
                  <input value={bank} onChange={(e) => setBank(e.target.value)} className="w-full rounded-md border border-edge bg-ink px-2.5 py-1.5 text-base text-slate-100 sm:text-sm" />
                </label>
                <label className="block text-sm text-slate-300">
                  <span className="mb-1 block">Last 4</span>
                  <input
                    value={mask}
                    inputMode="numeric"
                    maxLength={4}
                    onChange={(e) => setMask(e.target.value.replace(/\D/g, ""))}
                    className="w-full rounded-md border border-edge bg-ink px-2.5 py-1.5 text-base tabular-nums text-slate-100 sm:text-sm"
                  />
                </label>
              </div>
            )}
          </div>
          {(balance != null || card) && (
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={keepBalance} onChange={(e) => setKeepBalance(e.target.checked)} className="accent-sky-500" />
              {balance != null && accountKind === "bank" && <>Save the balance, {rupees(balance, { whole: true })}</>}
              {card && accountKind === "card" && (
                <>
                  Save the card {card.card_limit_paise ? `limit (${rupees(card.card_limit_paise, { whole: true })})` : ""}
                  {card.card_due_paise ? ` and the bill (${rupees(card.card_due_paise, { whole: true })}${card.card_due_on ? `, due ${dayLabel(card.card_due_on)}` : ""})` : ""}
                </>
              )}
            </label>
          )}
          {rows.length > 0 ? (
            <>
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <span className="flex-1">
                  {rows.length} found{rows.some((r) => r.seen) ? `; ${rows.filter((r) => r.seen).length} look like ones already in Money and are unticked` : ""}.
                  Check the amounts against the screenshots.
                </span>
                <button onClick={() => setRows((rs) => rs.map((r) => ({ ...r, keep: true })))} className="text-sky-300 hover:text-sky-200">
                  Tick all
                </button>
              </div>
              <ul className="max-h-96 divide-y divide-edge/60 overflow-y-auto rounded-lg border border-edge">
                {rows.map((r) => (
                  <li key={r.id} className={`flex items-center gap-2 px-2.5 py-1.5 text-sm ${r.keep ? "" : "opacity-60"}`}>
                    <input
                      type="checkbox"
                      checked={r.keep}
                      onChange={(e) => setRows((rs) => rs.map((x) => (x.id === r.id ? { ...x, keep: e.target.checked } : x)))}
                      className="accent-sky-500"
                      aria-label={`Add ${r.merchant}`}
                    />
                    <span className="w-16 shrink-0 text-xs text-slate-500">{dayLabel(r.occurred_on)}</span>
                    <input
                      value={r.merchant}
                      onChange={(e) => setRows((rs) => rs.map((x) => (x.id === r.id ? { ...x, merchant: e.target.value } : x)))}
                      className="min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 py-0.5 text-slate-100 hover:border-edge focus:border-sky-500 focus:outline-none"
                      aria-label="Merchant"
                    />
                    {r.seen && <span className="hidden shrink-0 text-xs text-slate-500 sm:inline">already in</span>}
                    <span className={`w-24 shrink-0 text-right tabular-nums ${r.direction === "credit" ? "text-slate-100" : "text-slate-200"}`}>
                      {r.direction === "credit" ? "+" : "−"}
                      {rupees(r.amount_paise).replace("−", "")}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="text-sm text-slate-500">No transactions on these screenshots{balance != null || card ? ", only the balance" : ""}.</p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={save}
              disabled={busy || (!kept.length && !(keepBalance && (balance != null || card))) || !target}
              className="rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-40"
            >
              {busy ? "Adding…" : kept.length ? `Add ${kept.length} transaction${kept.length === 1 ? "" : "s"}` : "Save"}
            </button>
            <button onClick={reset} className="rounded-md border border-edge px-3 py-1.5 text-sm text-slate-300 hover:bg-edge/60">
              Discard
            </button>
            {!target && <span className="text-xs text-slate-500">Pick the account these belong to.</span>}
          </div>
        </div>
      )}
    </Card>
  );
}

// --- Reading old alert emails ------------------------------------------------------------------------

/** One button to read old alert emails as far back as chosen, for history without statements. */
export function HistoryRead() {
  const [months, setMonths] = useState("12");
  const [busy, setBusy] = useState(false);
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-edge pt-3 text-xs text-slate-400">
      <History size={13} className="shrink-0" />
      <span>Read older alerts too, for history:</span>
      <Select value={months} onChange={(e) => setMonths(e.target.value)} aria-label="How far back">
        <option value="3">3 months</option>
        <option value="6">6 months</option>
        <option value="12">12 months</option>
        <option value="24">2 years</option>
      </Select>
      <button
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await api.moneyReadHistory(Number(months));
            toast("Reading old alert emails. A year of mail takes a few minutes; transactions appear as they're read.");
          } catch (e) {
            toast((e as Error).message, "error");
          } finally {
            setBusy(false);
          }
        }}
        className="rounded-md border border-edge px-2 py-0.5 text-slate-300 hover:bg-edge disabled:opacity-40"
      >
        Read back
      </button>
    </div>
  );
}

// --- Getting started -------------------------------------------------------------------------------

/** The first screen: the order that gets a full picture fastest when most banking happens in
 * phone apps. */
export function GettingStarted({ settings, onAddAccount }: { settings: MoneySettings | null; onAddAccount: () => void }) {
  const steps = [
    {
      icon: CreditCard,
      title: "Add your accounts and cards",
      body: "Bank, last four digits and today's balance from the app. Two minutes for all of them.",
      action: (
        <button onClick={onAddAccount} className="text-sky-300 hover:text-sky-200">
          Add an account
        </button>
      ),
    },
    {
      icon: Mail,
      title: "Switch on alert emails",
      body: `Every debit and credit alert your banks email you becomes a transaction, hourly. Then read the last 12 months for history.${settings && !settings.gmail_connected ? " Allow Gmail in Settings first." : ""}`,
      action: (
        <Link to="/money/setup" className="text-sky-300 hover:text-sky-200">
          Switch on
        </Link>
      ),
    },
    {
      icon: Smartphone,
      title: "Fill gaps with screenshots",
      body: "For anything without emails: screenshot the transaction list in the app and add it here. Read on your own computer.",
      action: (
        <Link to="/money/setup#screenshots" className="text-sky-300 hover:text-sky-200">
          Add screenshots
        </Link>
      ),
    },
    {
      icon: Landmark,
      title: "Add savings and loans",
      body: "FDs, RDs, PPF and EPF once; EMIs are picked up from card emails.",
      action: (
        <Link to="/money/savings" className="text-sky-300 hover:text-sky-200">
          Savings
        </Link>
      ),
    },
    {
      icon: ShieldCheck,
      title: "Set your saving target and cover",
      body: "So Health can tell you what's safe to spend and what to fix first.",
      action: (
        <Link to="/money/health" className="text-sky-300 hover:text-sky-200">
          Health
        </Link>
      ),
    },
  ];
  return (
    <div className="mx-auto max-w-2xl rounded-xl border border-edge bg-panel p-5">
      <h2 className="mb-1 text-lg font-semibold">Let's get your numbers in</h2>
      <p className="mb-4 text-sm text-slate-400">No bank login and no net banking needed. Each step adds to the others; nothing is counted twice.</p>
      <ol className="space-y-4 text-sm">
        {steps.map((s, i) => (
          <li key={s.title} className="flex gap-3">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-edge text-xs text-slate-300">{i + 1}</span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-1.5 font-medium text-slate-200">
                <s.icon size={14} className="text-slate-500" /> {s.title}
              </span>
              <span className="block text-slate-400">{s.body}</span>
              <span className="mt-0.5 block text-xs">{s.action}</span>
            </span>
          </li>
        ))}
      </ol>
      <p className="mt-4 flex items-center gap-1.5 text-xs text-slate-500">
        <CheckCircle2 size={12} /> Statements from net banking (CSV, Excel) and CAMS fund statements still work too, in Import and settings.
      </p>
    </div>
  );
}
