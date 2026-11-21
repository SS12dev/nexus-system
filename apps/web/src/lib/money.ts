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

export type SubStatus = "new" | "active" | "cancelled" | "ignored";
export type Cadence = "monthly" | "quarterly" | "yearly";

export interface Subscription {
  key: string;
  id: string | null;
  merchant: string;
  category: string;
  /** Subscriptions to review, or bills, rent and SIPs. */
  group: "subscription" | "bill";
  status: SubStatus;
  manual: boolean;
  detected: boolean;
  cadence: Cadence;
  amount_paise: number;
  monthly_paise: number;
  next_on: string | null;
  last_on: string | null;
  count: number;
  price_change_paise: number;
  previous_paise: number | null;
  charged_after_cancel: { on: string; amount_paise: number } | null;
  not_seen: boolean;
  remind_days: number;
  manage_url: string | null;
  note: string | null;
  cancelled_on: string | null;
}

export interface Subscriptions {
  items: Subscription[];
  subscriptions_monthly_paise: number;
  bills_monthly_paise: number;
  yearly_paise: number;
  saved_monthly_paise: number;
  to_review: number;
}

export interface SubscriptionSave {
  merchant: string;
  status?: Exclude<SubStatus, "new">;
  remind_days?: number;
  manage_url?: string | null;
  note?: string | null;
  manual?: boolean;
  amount_paise?: number;
  cadence?: Cadence;
  next_on?: string;
  category?: string;
  cancelled_on?: string;
}

export type BillStatus = "none" | "due" | "part" | "paid" | "overdue";

export interface AccountView {
  id: string;
  label: string;
  name: string;
  institution: string | null;
  mask: string | null;
  kind: "bank" | "card";
  hidden: boolean;
  count: number;
  last_on: string | null;
  balance_paise: number | null;
  balance_on: string | null;
  spent_month_paise: number;
  in_month_paise: number;
  // Cards only.
  credit_limit_paise?: number | null;
  available_paise?: number | null;
  available_on?: string | null;
  due_paise?: number | null;
  min_due_paise?: number | null;
  due_on?: string | null;
  statement_on?: string | null;
  paid_on?: string | null;
  paid_since_statement_paise?: number;
  bill?: { status: BillStatus; left_paise: number };
  owed_paise?: number | null;
  owed_from_limit?: boolean;
  emis?: { id: string; name: string; emi_paise: number }[];
}

export interface AccountEdit {
  name?: string;
  hidden?: boolean;
  balance_paise?: number | null;
  credit_limit_paise?: number | null;
  due_paise?: number | null;
  min_due_paise?: number | null;
  due_on?: string | null;
  paid?: boolean;
}

export type LoanKind = "card_emi" | "personal" | "home" | "vehicle" | "education" | "other";

export interface Loan {
  id: string;
  kind: LoanKind;
  kind_label: string;
  name: string;
  account_id: string | null;
  account: string | null;
  lender: string | null;
  principal_paise: number;
  rate_bp: number;
  months: number;
  emi_paise: number;
  first_due_on: string;
  status: "active" | "closed";
  note: string | null;
  paid: number;
  left: number;
  outstanding_paise: number;
  next_due_on: string | null;
  ends_on: string;
  total_interest_paise: number;
  interest_left_paise: number;
  done: boolean;
}

export interface EmiSeen {
  merchant: string;
  account_id: string | null;
  account: string | null;
  monthly_paise: number;
  months_seen: number;
  first_on: string;
  last_on: string;
}

export interface Loans {
  items: Loan[];
  monthly_paise: number;
  outstanding_paise: number;
  untracked: EmiSeen[];
}

export interface Balances {
  items: AccountView[];
  cash_paise: number;
  owed_paise: number;
  unknown_balances: number;
  loans: Loans;
}

export interface LoanIn {
  kind: LoanKind;
  name: string;
  account_id?: string | null;
  lender?: string | null;
  principal_paise: number;
  rate_bp: number;
  months: number;
  emi_paise?: number | null;
  first_due_on: string;
  note?: string | null;
}

export type AssetKind = "fd" | "rd" | "ppf" | "epf" | "nps" | "gold" | "other";

export interface Asset {
  id: string;
  kind: AssetKind;
  kind_label: string;
  name: string;
  institution: string | null;
  amount_paise: number;
  rate_bp: number;
  start_on: string | null;
  maturity_on: string | null;
  balance_on: string | null;
  contribution_paise: number;
  payout: boolean;
  status: "active" | "closed";
  note: string | null;
  value_paise: number;
  maturity_value_paise: number | null;
  monthly_paise: number;
  matured: boolean;
}

export interface AssetIn {
  kind: AssetKind;
  name?: string;
  institution?: string | null;
  amount_paise: number;
  rate_bp?: number;
  start_on?: string | null;
  maturity_on?: string | null;
  balance_on?: string | null;
  contribution_paise?: number;
  payout?: boolean;
  status?: "active" | "closed";
  note?: string | null;
}

export interface Savings {
  items: Asset[];
  total_paise: number;
  monthly_paise: number;
  funds_value_paise: number;
  funds_cost_paise: number;
  maturing: Asset[];
  kinds: Record<AssetKind, string>;
  default_rates: Partial<Record<AssetKind, number>>;
}

export interface Assumptions {
  income_paise: number | null;
  spend_paise: number | null;
  income_growth_bp: number;
  inflation_bp: number;
  fund_return_bp: number;
  fund_low_bp: number;
  fund_high_bp: number;
  new_sip_paise: number;
  emergency_months: number;
  save_target_bp: number;
  health_cover_paise: number | null;
  life_cover_paise: number | null;
  dependants: boolean | null;
}

export type HealthStatus = "good" | "watch" | "act" | "unknown";

export interface HealthCheck {
  key: "emergency" | "saving" | "emis" | "cards" | "bills" | "health" | "life";
  title: string;
  status: HealthStatus;
  value: string;
  target: string;
  body: string;
  link: string | null;
}

export interface Health {
  verdict: "good" | "watch" | "act";
  counts: Record<HealthStatus, number>;
  checks: HealthCheck[];
  next_step: HealthCheck | null;
  safe_to_spend: {
    known: boolean;
    expected_income_paise: number;
    keep_paise: number;
    spent_paise: number;
    committed_paise: number;
    left_paise: number;
    per_day_paise: number;
    days_left: number;
    status: HealthStatus;
  };
  balance: {
    income_paise: number;
    essentials_paise: number;
    lifestyle_paise: number;
    saved_paise: number;
    guide: { essentials: number; lifestyle: number; saved: number };
  };
  ready_to_invest: boolean;
  months_of_history: number;
  targets: Pick<Assumptions, "save_target_bp" | "emergency_months" | "health_cover_paise" | "life_cover_paise" | "dependants">;
  unknown_balances: number;
}

/** Each part of a month as a share of income, for the essentials / lifestyle / saved balance.
 * Saved can be negative (more out than in); shares are of income, so they add up to 1. */
export function balanceShares(b: Health["balance"]): { essentials: number; lifestyle: number; saved: number } | null {
  if (b.income_paise <= 0) return null;
  return {
    essentials: b.essentials_paise / b.income_paise,
    lifestyle: b.lifestyle_paise / b.income_paise,
    saved: b.saved_paise / b.income_paise,
  };
}

export interface ForecastPoint {
  month: string;
  net_worth_paise: number;
  low_paise: number;
  high_paise: number;
  cash_paise: number;
  funds_paise: number;
  savings_paise: number;
  debt_paise: number;
  emis_paise: number;
}

export interface Goal {
  id: string;
  name: string;
  target_paise: number;
  by_on: string;
  saved_paise: number;
  asset_ids: string[];
  have_paise: number;
  months_left: number;
  rate_bp: number;
  monthly_needed_paise: number;
  progress: number;
}

export interface Insight {
  tone: "good" | "warning" | "info";
  title: string;
  body: string;
}

export interface Plan {
  start: { net_worth_paise: number; cash_paise: number; funds_paise: number; savings_paise: number; debt_paise: number };
  inputs: {
    income_paise: number;
    spend_paise: number;
    emis_paise: number;
    contributions_paise: number;
    existing_investing_paise: number;
    surplus_paise: number;
    surplus_spread_paise: number;
    months_of_history: number;
  };
  points: ForecastPoint[];
  assumptions: Assumptions;
  saved_assumptions: Partial<Assumptions>;
  accounts_known: number;
  unknown_balances: number;
  liquid_paise: number;
  emergency_months: number | null;
  goals: Goal[];
  goals_monthly_paise: number;
  insights: Insight[];
}

export interface Upcoming {
  kind: "subscription" | "card" | "emi";
  label: string;
  amount_paise: number;
  on: string;
  detail: string;
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
  subscriptions_to_review: number;
  upcoming: Upcoming[];
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

/** Basis points -> "7.1%". */
export function percent(bp: number): string {
  return `${Number((bp / 100).toFixed(2))}%`;
}

/** A percentage typed by a person -> basis points, or null: "7.1" -> 710. */
export function parsePercent(text: string): number | null {
  const clean = text.replace(/[%\s]/g, "");
  if (!/^\d{1,2}(\.\d{1,2})?$/.test(clean)) return null;
  return Math.round(Number(clean) * 100);
}

/** "2027-03" -> "Mar 2027". */
export function shortMonth(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return `${MONTHS[m - 1]} ${y}`;
}

/** "2026-11-05" -> "5 Nov 2026". */
export function fullDay(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

/** Days from today to an ISO date (negative when it has passed). */
export function daysUntil(day: string, now = new Date()): number {
  const [y, m, d] = day.split("-").map(Number);
  const then = Date.UTC(y, m - 1, d);
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((then - today) / 86_400_000);
}

/** "today", "tomorrow", "in 5 days", "3 days ago". */
export function whenLabel(day: string, now = new Date()): string {
  const n = daysUntil(day, now);
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  if (n === -1) return "yesterday";
  return n > 0 ? `in ${n} days` : `${-n} days ago`;
}

export const CADENCE_LABEL: Record<Cadence, string> = { monthly: "Monthly", quarterly: "Every 3 months", yearly: "Yearly" };

export const BILL_LABEL: Record<BillStatus, string> = {
  none: "No bill read yet",
  due: "Due",
  part: "Part paid",
  paid: "Paid",
  overdue: "Overdue",
};

/** The reducing-balance EMI (the same sum as the server's): a no-cost EMI splits evenly. */
export function emiFor(principal: number, rateBp: number, months: number): number {
  if (months <= 0) return principal;
  const r = rateBp / 120000;
  if (r === 0) return Math.ceil(principal / months);
  const f = (1 + r) ** months;
  return Math.round((principal * r * f) / (f - 1));
}

/** Today as YYYY-MM-DD in the browser's time zone. */
export function isoToday(now = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

/** The same day `months` later (the month's last day when it's shorter), as YYYY-MM-DD. */
export function addMonths(day: string, months: number): string {
  const [y, m, d] = day.split("-").map(Number);
  const n = y * 12 + (m - 1) + months;
  const yy = Math.floor(n / 12);
  const mm = (n % 12) + 1;
  const last = new Date(yy, mm, 0).getDate();
  return `${yy}-${String(mm).padStart(2, "0")}-${String(Math.min(d, last)).padStart(2, "0")}`;
}
