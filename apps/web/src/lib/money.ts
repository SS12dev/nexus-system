/** Money: types for /api/money and the small helpers its pages share. Amounts travel as whole
 * paise (integers) and are only turned into rupees for display. */

export type Direction = "debit" | "credit";

export interface MoneySettings {
  gmail_sync: boolean;
  assistant_access: boolean;
  cas_password_set: boolean;
  gmail_connected: boolean;
  gmail_synced_at: string | null;
  cas_synced_at: string | null;
  last_error: string | null;
  rules: number;
  categories: string[];
}

export interface MoneyTxn {
  id: string;
  account_id: string | null;
  occurred_on: string;
  amount_paise: number;
  direction: Direction;
  merchant: string;
  category: string;
  category_by: "auto" | "rule" | "user";
  channel: string;
  source: "alert" | "statement" | "manual";
  confirmed: boolean;
  excluded: boolean;
  note: string;
  narration?: string | null;
}

export interface TxnPage {
  items: MoneyTxn[];
  total: number;
  spent_paise: number;
  income_paise: number;
}

export interface TxnQuery {
  month?: string;
  category?: string;
  account_id?: string;
  direction?: Direction;
  merchant?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

export interface MoneyAccount {
  id: string;
  institution: string | null;
  mask: string | null;
  kind: "bank" | "card";
  name: string;
  label: string;
  count: number;
  last_on: string | null;
}

export interface Subscription {
  merchant: string;
  category: string;
  cadence: "monthly" | "yearly";
  typical_paise: number;
  last_on: string;
  next_on: string;
  count: number;
  monthly_paise: number;
}

export interface BudgetLine {
  category: string;
  monthly_paise: number;
  spent_paise: number;
}

export interface MoneyOverview {
  month: string;
  spent_paise: number;
  income_paise: number;
  net_paise: number;
  categories: { category: string; spent_paise: number }[];
  merchants: { merchant: string; spent_paise: number; count: number }[];
  count: number;
  trend: { month: string; spent_paise: number; income_paise: number }[];
  budgets: BudgetLine[];
  month_progress: number;
  subscriptions_monthly_paise: number;
  upcoming: Subscription[];
  /** Put into investments this month (SIPs and the like). */
  invested_paise: number;
  funds_value_paise: number;
  funds_cost_paise: number;
  has_data: boolean;
  gmail_sync: boolean;
  gmail_synced_at: string | null;
}

export interface Holding {
  id: string;
  folio: string;
  amc: string;
  scheme: string;
  isin: string | null;
  units: number;
  nav: number | null;
  nav_date: string | null;
  value_paise: number | null;
  cost_paise: number | null;
  as_of: string | null;
}

export interface Investments {
  items: Holding[];
  value_paise: number;
  cost_paise: number;
  gain_paise: number | null;
  as_of: string | null;
  nav_date: string | null;
}

export interface MoneyImport {
  id: string;
  kind: "statement" | "cas";
  origin: "upload" | "gmail";
  filename: string;
  status: "done" | "failed";
  counts: Record<string, number>;
  error: string | null;
  created_at: string;
}

/** Indian digit grouping: 12345678 paise -> "₹1,23,456.78"; `whole` drops the paise. */
export function rupees(paise: number, opts: { whole?: boolean; sign?: boolean } = {}): string {
  const neg = paise < 0;
  const abs = Math.abs(paise);
  const whole = opts.whole ? Math.round(abs / 100) : Math.floor(abs / 100);
  const digits = String(whole);
  const grouped =
    digits.length > 3
      ? digits.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + digits.slice(-3)
      : digits;
  const frac = opts.whole ? "" : "." + String(abs % 100).padStart(2, "0");
  const sign = neg ? "−" : opts.sign && paise > 0 ? "+" : "";
  return `${sign}₹${grouped}${frac}`;
}

/** Short amounts for chart labels: ₹950, ₹12.4k, ₹1.2L, ₹3.4Cr. */
export function compact(paise: number): string {
  const r = Math.abs(paise) / 100;
  const sign = paise < 0 ? "−" : "";
  if (r >= 1e7) return `${sign}₹${trim(r / 1e7)}Cr`;
  if (r >= 1e5) return `${sign}₹${trim(r / 1e5)}L`;
  if (r >= 1e3) return `${sign}₹${trim(r / 1e3)}k`;
  return `${sign}₹${Math.round(r)}`;
}

function trim(n: number): string {
  return n >= 100 ? String(Math.round(n)) : n.toFixed(1).replace(/\.0$/, "");
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function shiftMonth(month: string, by: number): string {
  const [y, m] = month.split("-").map(Number);
  const n = y * 12 + (m - 1) + by;
  return `${Math.floor(n / 12)}-${String((n % 12) + 1).padStart(2, "0")}`;
}

/** "2026-11" -> "November 2026"; short: "Nov". */
export function monthLabel(month: string, short = false): string {
  const [y, m] = month.split("-").map(Number);
  const name = MONTHS[m - 1];
  if (short) return name;
  return `${new Date(y, m - 1, 1).toLocaleString("en-GB", { month: "long" })} ${y}`;
}

export function thisMonth(now = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

/** "2026-11-05" -> "5 Nov". */
export function dayLabel(day: string): string {
  const [, m, d] = day.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]}`;
}

/** Rupees typed by a person -> paise, or null: "1,250.5" -> 125050. */
export function parseRupees(text: string): number | null {
  const clean = text.replace(/[₹,\s]/g, "").replace(/^rs\.?/i, "");
  if (!/^\d+(\.\d{1,2})?$/.test(clean)) return null;
  const [w, f = ""] = clean.split(".");
  return Number(w) * 100 + Number(f.padEnd(2, "0"));
}

export type BudgetState = "over" | "ahead" | "ok";

/** Over the limit, spending faster than the month is passing (by more than 10%), or fine. */
export function budgetState(b: BudgetLine, progress: number): BudgetState {
  if (b.spent_paise > b.monthly_paise) return "over";
  if (b.spent_paise / b.monthly_paise > Math.min(1, progress + 0.1)) return "ahead";
  return "ok";
}

export const CHANNEL_LABEL: Record<string, string> = {
  upi: "UPI",
  card: "Card",
  atm: "Cash withdrawal",
  neft: "NEFT",
  imps: "IMPS",
  cash: "Cash",
  other: "Bank",
};

export const SOURCE_LABEL: Record<MoneyTxn["source"], string> = {
  alert: "From an alert email",
  statement: "From a statement",
  manual: "Added by you",
};
