import { AlertTriangle, CheckCircle2, Eye, EyeOff, FileSpreadsheet, FileUp, KeyRound, Loader2, Lock, Mail, Pencil, RefreshCw, Sparkles, Trash2, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { api } from "../../lib/api";
import { formatAgo } from "../../lib/format";
import { dayLabel, type MoneyImport } from "../../lib/money";
import { confirmAction, toast } from "../Feedback";
import { Select } from "../Select";
import { HistoryRead, ScreenshotImport } from "./Onboard";
import { Card, Spinner, Toggle, useAccounts, useMoney, useMoneySettings } from "./parts";

function importSummary(i: MoneyImport): string {
  if (i.status === "failed") return i.error ?? "Couldn't be read";
  if (i.kind === "cas") return `${i.counts.holdings ?? 0} fund holdings`;
  if (i.kind === "screenshot") {
    const already = (i.counts.matched ?? 0) + (i.counts.skipped ?? 0);
    return `${i.counts.added ?? 0} new${already ? `, ${already} already in Money` : ""}`;
  }
  const bits = [`${i.counts.added ?? 0} new`];
  if (i.counts.matched) bits.push(`${i.counts.matched} matched to alert emails`);
  if (i.counts.skipped) bits.push(`${i.counts.skipped} already here`);
  return bits.join(", ");
}

/** Statement and CAS uploads, alert-email sync, the CAS password, the assistant's access,
 * account names, and deleting everything. */
export function SetupView() {
  const { data: settings, reload } = useMoneySettings();
  const ready = !!settings;
  useEffect(() => {
    // "Add screenshots" on the first screen links here with #screenshots.
    if (ready && window.location.hash) document.querySelector(window.location.hash)?.scrollIntoView({ block: "start" });
  }, [ready]);
  if (!settings) return <Spinner />;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <AlertSync settings={settings} onChange={reload} />
      <div id="screenshots" className="contents">
        <ScreenshotImport />
      </div>
      <Upload casPasswordSet={settings.cas_password_set} />
      <Imports />
      <CasPassword set={settings.cas_password_set} onChange={reload} />
      <Accounts />
      <div id="export" className="contents">
        <Export />
      </div>
      <Assistant on={settings.assistant_access} onChange={reload} />
      <Privacy />
    </div>
  );
}

function Upload({ casPasswordSet }: { casPasswordSet: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const accounts = useAccounts();
  const [file, setFile] = useState<File | null>(null);
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [account, setAccount] = useState("");
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const pdf = file?.name.toLowerCase().endsWith(".pdf") ?? false;

  const send = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const r = await api.moneyImport(file, { password, remember, account_id: account || undefined });
      toast(`${file.name}: ${importSummary(r)}`);
      setFile(null);
      setPassword("");
      if (input.current) input.current.value = "";
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Add a statement">
      <p className="mb-3 text-sm text-slate-400">
        A bank or card statement downloaded from net banking (<b className="text-slate-300">CSV or Excel .xlsx</b>), or a
        mutual fund <b className="text-slate-300">CAS PDF</b> from CAMS or KFintech. Files are read here and thrown away;
        only the transactions are kept. Importing the same file twice adds nothing.
      </p>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          const f = e.dataTransfer.files[0];
          if (f) setFile(f);
        }}
        className={`flex flex-col items-center gap-2 rounded-lg border border-dashed px-3 py-5 text-center text-sm ${drag ? "border-sky-400 bg-sky-500/10" : "border-edge"}`}
      >
        <FileUp size={20} className="text-slate-400" />
        {file ? <span className="break-all text-slate-200">{file.name}</span> : <span className="text-slate-400">Drop a file here, or</span>}
        <button onClick={() => input.current?.click()} className="rounded-md border border-edge px-3 py-1 text-sm text-slate-200 hover:bg-edge">
          {file ? "Choose another" : "Choose a file"}
        </button>
        <input
          ref={input}
          type="file"
          accept=".csv,.tsv,.txt,.xlsx,.xlsm,.pdf,.xls"
          className="hidden"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
      </div>
      {file && (
        <div className="mt-3 space-y-2">
          {pdf ? (
            <>
              <label className="grid gap-1 text-xs text-slate-400">
                PDF password {casPasswordSet && "(leave empty to use the saved one)"}
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="off"
                  placeholder="Often your PAN, in capitals"
                  className="rounded-md border border-edge bg-ink px-2.5 py-1.5 text-base sm:text-sm"
                />
              </label>
              {password && (
                <label className="flex items-center gap-1.5 text-xs text-slate-400">
                  <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} className="accent-sky-500" />
                  Remember it (encrypted), so emailed statements can be read automatically
                </label>
              )}
            </>
          ) : (
            accounts.length > 0 && (
              <label className="grid gap-1 text-xs text-slate-400">
                Which account is it for?
                <Select value={account} onChange={(e) => setAccount(e.target.value)}>
                  <option value="">Work it out from the file</option>
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.label}
                    </option>
                  ))}
                </Select>
              </label>
            )
          )}
          <button
            onClick={send}
            disabled={busy}
            className="flex items-center gap-1.5 rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-50"
          >
            {busy && <Loader2 size={14} className="animate-spin" />} Import
          </button>
        </div>
      )}
    </Card>
  );
}

function AlertSync({ settings, onChange }: { settings: NonNullable<ReturnType<typeof useMoneySettings>["data"]>; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const set = async (gmail_sync: boolean) => {
    setBusy(true);
    try {
      await api.moneyUpdateSettings({ gmail_sync });
      toast(gmail_sync ? "Reading alert emails from the last 90 days now. Read further back below." : "Alert emails won't be read");
      onChange();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card
      title="Bank and card alert emails"
      action={<Toggle checked={settings.gmail_sync} onChange={set} label="Read alert emails" disabled={busy || (!settings.gmail_connected && !settings.gmail_sync)} />}
    >
      <p className="text-sm text-slate-400">
        Every hour, new transaction alerts in your Gmail (“Rs 250 debited from a/c XX1234…”) become transactions, so this
        stays current without uploading anything. Works with HDFC, ICICI, SBI, Axis, Kotak and most other Indian banks and
        cards. One-time passwords, reminders and declined payments are skipped. When a statement arrives later, its rows
        are matched to the alerts instead of counted twice.
      </p>
      {!settings.gmail_connected ? (
        <p className="mt-3 flex items-center gap-1.5 text-sm text-amber-200">
          <Mail size={14} /> Gmail isn't connected.{" "}
          <Link to="/settings" className="text-sky-300 hover:text-sky-200">
            Allow Gmail in Settings
          </Link>
        </p>
      ) : settings.gmail_sync ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-400">
          {settings.last_error ? (
            <span className="flex items-center gap-1 text-[#fab219]">
              <AlertTriangle size={13} /> {settings.last_error}
            </span>
          ) : (
            <span className="flex items-center gap-1">
              <CheckCircle2 size={13} className="text-[#0ca30c]" />
              {settings.gmail_synced_at ? `Checked ${formatAgo(settings.gmail_synced_at)}` : "First check running"}
            </span>
          )}
          <button
            onClick={async () => {
              try {
                await api.moneySync();
                toast("Checking now. New transactions appear in a minute or so.");
              } catch (e) {
                toast((e as Error).message, "error");
              }
            }}
            className="ml-auto flex items-center gap-1 rounded-md border border-edge px-2 py-0.5 text-slate-300 hover:bg-edge"
          >
            <RefreshCw size={12} /> Check now
          </button>
        </div>
      ) : null}
      {settings.gmail_connected && settings.gmail_sync && <HistoryRead />}
    </Card>
  );
}

function Imports() {
  const { data } = useMoney(api.moneyImports);
  if (!data?.length) return null;
  return (
    <Card title="Recent imports">
      <ul className="space-y-2 text-sm">
        {data.slice(0, 8).map((i) => (
          <li key={i.id} className="flex items-start gap-2">
            {i.status === "done" ? (
              <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-[#0ca30c]" aria-label="Read" />
            ) : (
              <XCircle size={14} className="mt-0.5 shrink-0 text-[#d03b3b]" aria-label="Failed" />
            )}
            <span className="min-w-0 flex-1">
              <span className="block truncate text-slate-200">{i.filename}</span>
              <span className="block text-xs text-slate-500">
                {i.origin === "gmail" ? "From Gmail · " : ""}
                {importSummary(i)} · {formatAgo(i.created_at)}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function CasPassword({ set, onChange }: { set: boolean; onChange: () => void }) {
  const [value, setValue] = useState("");
  const [editing, setEditing] = useState(false);
  const save = async (cas_password: string) => {
    try {
      await api.moneyUpdateSettings({ cas_password });
      toast(cas_password ? "Saved" : "Forgotten");
      setValue("");
      setEditing(false);
      onChange();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <Card title="Mutual fund statements (CAS)">
      <p className="text-sm text-slate-400">
        Ask CAMS for a <b className="text-slate-300">Detailed</b> consolidated account statement (camsonline.com → Statements →
        CAS - CAMS+KFintech) covering all your funds. Upload the PDF above, or, with alert emails on and the password saved
        here, the emailed PDF is read automatically. Fund values update every night from AMFI's published prices.
      </p>
      {editing || !set ? (
        <form
          className="mt-3 flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (value.trim()) void save(value.trim());
          }}
        >
          <KeyRound size={15} className="shrink-0 text-slate-500" />
          <input
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoComplete="off"
            placeholder="CAS PDF password (often your PAN)"
            className="min-w-0 flex-1 rounded-md border border-edge bg-ink px-2.5 py-1 text-base sm:text-sm"
            aria-label="CAS PDF password"
          />
          <button type="submit" disabled={!value.trim()} className="rounded-md bg-sky-500 px-3 py-1 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-40">
            Save
          </button>
        </form>
      ) : (
        <p className="mt-3 flex items-center gap-2 text-sm text-slate-300">
          <Lock size={14} className="text-slate-500" /> Password saved, encrypted
          <button onClick={() => setEditing(true)} className="ml-auto text-xs text-sky-300 hover:text-sky-200">
            Change
          </button>
          <button onClick={() => save("")} className="text-xs text-slate-400 hover:text-slate-200">
            Forget
          </button>
        </p>
      )}
    </Card>
  );
}

function Accounts() {
  const accounts = useAccounts();
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");
  if (!accounts.length) return null;
  return (
    <Card title="Accounts and cards">
      <p className="mb-2 text-xs text-slate-500">Found from alert emails and statements, by bank and last four digits. Give them names you'll recognise.</p>
      <ul className="divide-y divide-edge/60 text-sm">
        {accounts.map((a) => (
          <li key={a.id} className="flex items-center gap-2 py-2">
            {editing === a.id ? (
              <form
                className="flex flex-1 items-center gap-2"
                onSubmit={async (e) => {
                  e.preventDefault();
                  try {
                    await api.moneyRenameAccount(a.id, name);
                    setEditing(null);
                    toast("Renamed");
                  } catch (err) {
                    toast((err as Error).message, "error");
                  }
                }}
              >
                <input
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={120}
                  placeholder={a.institution ?? "Name"}
                  className="min-w-0 flex-1 rounded-md border border-edge bg-ink px-2 py-1 text-base sm:text-sm"
                  aria-label="Account name"
                />
                <button type="submit" className="text-xs text-sky-300">
                  Save
                </button>
              </form>
            ) : (
              <>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-slate-200">{a.label}</span>
                  <span className="block text-xs text-slate-500">
                    {a.kind === "card" ? "Card" : "Bank account"}
                    {a.mask && ` ending ${a.mask}`} · {a.count} transactions
                    {a.last_on && ` · last ${dayLabel(a.last_on)}`}
                  </span>
                </span>
                <button
                  onClick={() => {
                    setEditing(a.id);
                    setName(a.name);
                  }}
                  className="p-1 text-slate-400 hover:text-white"
                  aria-label={`Rename ${a.label}`}
                >
                  <Pencil size={13} />
                </button>
              </>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Assistant({ on, onChange }: { on: boolean; onChange: () => void }) {
  return (
    <Card
      title="Your assistant and Money"
      action={
        <Toggle
          checked={on}
          label="Let the assistant use Money"
          onChange={async (assistant_access) => {
            try {
              await api.moneyUpdateSettings({ assistant_access });
              onChange();
            } catch (e) {
              toast((e as Error).message, "error");
            }
          }}
        />
      }
    >
      <p className="flex gap-2 text-sm text-slate-400">
        <Sparkles size={15} className="mt-0.5 shrink-0 text-sky-300" />
        <span>
          On by default. Your assistant can answer “how much did I spend on food?”, “when is my card bill due?” or “what
          if I invest ₹10,000 a month?” from your transactions (date, amount, merchant, category), balances, card bills,
          savings, loans, subscriptions and the forecast. What it reads is sent to the AI model like any other chat. It
          never sees the bank's own wording, full account numbers or statements. The one thing it can change is what
          you've decided about a subscription; it can't move money.
        </span>
      </p>
    </Card>
  );
}

/** Everything in Money as one Excel workbook, optionally locked with a password. */
function Export() {
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const download = async () => {
    setBusy(true);
    try {
      const { blob, name } = await api.moneyExport(password);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      toast(password ? `Downloaded ${name}, locked with your password` : `Downloaded ${name}`);
      setPassword("");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Export my finances">
      <p className="text-sm text-slate-400">
        One Excel workbook with everything in Money: a summary with your net worth, accounts and cards, savings and deposits (with account
        numbers, maturity dates and nominees), mutual funds, loans and EMIs, subscriptions and insurance. For your own records, or so
        your family knows what you have and where to claim it.
      </p>
      <label htmlFor="export-password" className="mt-3 block text-sm text-slate-300">
        Password to open the file <span className="text-slate-500">(recommended)</span>
      </label>
      <span className="relative mt-1 block">
        <input
          id="export-password"
          type={visible ? "text" : "password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
          maxLength={128}
          placeholder="Leave empty for an unlocked file"
          className="w-full rounded-md border border-edge bg-ink px-2.5 py-1.5 pr-9 text-base text-slate-100 placeholder:text-slate-600 focus:border-sky-500 focus:outline-none sm:text-sm"
        />
        <button
          type="button"
          onClick={() => setVisible((x) => !x)}
          className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded p-1 text-slate-400 hover:text-white"
          aria-label={visible ? "Hide the password" : "Show the password"}
        >
          {visible ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
      </span>
      <p className="mt-1 text-xs text-slate-500">
        The file holds full account numbers, so lock it if it will leave this device. Excel, Numbers and LibreOffice ask for the password
        to open it (AES-256). Nexus doesn't keep the password: if it's forgotten, export again.
      </p>
      <button
        onClick={download}
        disabled={busy}
        className="mt-3 flex items-center gap-1.5 rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-50"
      >
        {busy ? <Loader2 size={14} className="animate-spin" /> : <FileSpreadsheet size={14} />} {password ? "Download locked Excel" : "Download Excel"}
      </button>
      <p className="mt-2 text-xs text-slate-500">Each export is noted in the audit log.</p>
    </Card>
  );
}

function Privacy() {
  return (
    <Card title="Privacy and deleting">
      <ul className="list-disc space-y-1 pl-4 text-sm text-slate-400">
        <li>Everything stays on your own server. No bank login, no aggregator, no third party.</li>
        <li>Statements and alerts are read with fixed rules here; no AI model sees them.</li>
        <li>
          The bank's wording of each transaction, file names, your CAS password, and account numbers and nominees on savings and loans are
          encrypted in the database.
        </li>
        <li>Only you (the owner) can open Money. It isn't in search, the Admin pages or the logs.</li>
        <li>Read-only: nothing here can move money.</li>
      </ul>
      <button
        onClick={async () => {
          const ok = await confirmAction({
            title: "Delete everything in Money?",
            body: "All transactions, accounts, budgets, fund holdings, rules, the saved CAS password and import history. Alert-email reading is switched off. This can't be undone.",
            confirmLabel: "Delete everything",
            danger: true,
          });
          if (!ok) return;
          try {
            await api.moneyDeleteAll();
            toast("Money is empty");
          } catch (e) {
            toast((e as Error).message, "error");
          }
        }}
        className="mt-3 flex items-center gap-1.5 rounded-md border border-rose-500/40 px-2.5 py-1 text-sm text-rose-300 hover:bg-rose-500/10"
      >
        <Trash2 size={14} /> Delete all Money data
      </button>
    </Card>
  );
}
