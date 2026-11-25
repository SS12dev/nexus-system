import type {
  AccountEdit,
  Advice,
  AdviceAction,
  OrderPaid,
  OrderToConfirm,
  Suggestions,
  AccountView,
  Asset,
  AssetIn,
  Assumptions,
  Health,
  NewAccount,
  ScreenshotReading,
  ScreenshotSave,
  Balances,
  BoughtItem,
  OrderReading,
  ReadItem,
  Daily,
  Investments,
  Loan,
  LoanIn,
  Loans,
  MoneyAccount,
  MoneyImport,
  MoneyOverview,
  MoneySettings,
  MoneyTxn,
  Plan,
  Tracking,
  MailStatement,
  Salaries,
  SalaryIn,
  Savings,
  SecretDetails,
  TxnMatches,
  Subscription,
  SubscriptionSave,
  Subscriptions,
  TxnPage,
  TxnQuery,
  NewStock,
  Stock,
  StockEdit,
  StockFound,
  Stocks,
  Growth,
  GrowthRange,
} from "./money";
import type { BriefPage, BriefSettings, Place } from "./brief";
import type { TuneId } from "./tunes";
import type {
  Universe,
  AccessRequest,
  AccessRequestList,
  AccessState,
  AccountRecoveryList,
  AccountRecoveryRequest,
  AuthConfig,
  AppItem,
  AgentSystem,
  Approval,
  AuditEntry,
  Backup,
  Chat,
  Channel,
  ConfigValue,
  ConfigView,
  FeedbackItem,
  FeedbackKind,
  FileItem,
  FindKind,
  FindResponse,
  OrgItem,
  OrgItemDetail,
  OrgItemInput,
  OrgListQuery,
  OrgOverview,
  OrgReview,
  OrgRevision,
  OrgToday,
  TelegramStatus,
  Folder,
  GoogleAccess,
  LinkItem,
  Grants,
  Instructions,
  Memory,
  MemoryEvent,
  MemoryKind,
  Message,
  Notice,
  BridgeCall,
  ScreenMode,
  ScreenState,
  Widget,
  WidgetPos,
  WidgetSize,
  SearchEngine,
  WebPage,
  WebResult,
  WidgetAction,
  WidgetInput,
  OnboardingAnswers,
  OpsStatus,
  Profile,
  Profiles,
  Project,
  Role,
  Routine,
  RoutineInput,
  RoutineRun,
  Run,
  SchedulePreview,
  RunDetail,
  SearchHit,
  SessionInfo,
  Skill,
  SkillDetail,
  SkillVersion,
  SystemDetail,
  TrashItem,
  ToolEntry,
  Alerts,
  Improvements,
  GraphAdmin,
  KnowledgeGraph,
  GraphCard,
  CardAnswer,
  ChatImportItem,
  LlmQuery,
  LlmUsage,
  Monitoring,
  Proposal,
  RecallCheck,
  RecallSummary,
  Review,
  Usage,
  User,
  UserStatus,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(path, {
    credentials: "same-origin",
    ...init,
    // FormData bodies (uploads) set their own multipart Content-Type.
    headers:
      typeof init.body === "string"
        ? { "Content-Type": "application/json", ...init.headers }
        : init.headers,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      // Unexpected server errors name the request, so the owner can find it in the logs.
      if (typeof body.request_id === "string") detail += ` (ref ${body.request_id})`;
    } catch {
      /* not JSON */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp.status === 204 ? (undefined as T) : resp.json();
}

const json = (body: unknown) => JSON.stringify(body);

/** Tell the server a page crashed, for the Admin page's recent errors. Best effort, silent. */
export function reportClientError(error: Error, page: string): void {
  fetch("/api/me/client-error", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: json({
      message: `${error.name}: ${error.message}`.slice(0, 1000),
      stack: (error.stack ?? "").slice(-4000),
      page: page.slice(0, 300),
    }),
  }).catch(() => {});
}

/** Admin stats narrowed to these people: one user_id per person, none for everyone. */
function forUsers(ids: string[]): URLSearchParams {
  const q = new URLSearchParams();
  for (const id of ids) q.append("user_id", id);
  return q;
}

/** A save based on an older version: the server's current copy comes with it. */
export class OrgConflict extends ApiError {
  constructor(
    message: string,
    public current: OrgItem,
  ) {
    super(409, message);
  }
}

async function orgSave(path: string, method: string, body: unknown): Promise<OrgItem> {
  const resp = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: json(body),
  });
  if (resp.status === 409) {
    const data = await resp.json();
    throw new OrgConflict(data.detail, data.current);
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp.json();
}

function orgQuery(q: OrgListQuery | TxnQuery | Record<string, string | number | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const api = {
  orgOverview: () => request<OrgOverview>("/api/organiser/overview"),
  orgToday: () => request<OrgToday>("/api/organiser/today"),
  orgDaily: (day: string) => request<OrgItem>(`/api/organiser/daily/${day}`),
  orgItems: (q: OrgListQuery = {}) => request<OrgItem[]>(`/api/organiser/items${orgQuery(q)}`),
  orgItem: (id: string) => request<OrgItemDetail>(`/api/organiser/items/${id}`),
  orgCreate: (body: OrgItemInput) =>
    request<OrgItem>("/api/organiser/items", { method: "POST", body: json(body) }),
  /** Quick capture; to the Inbox unless `inbox: false`. */
  orgCapture: (text: string, opts: { inbox?: boolean; task?: boolean; parent_id?: string } = {}) =>
    request<OrgItem>("/api/organiser/capture", { method: "POST", body: json({ text, ...opts }) }),
  /** Throws OrgConflict when `version` is outdated. */
  orgUpdate: (id: string, changes: OrgItemInput & { version?: number }) =>
    orgSave(`/api/organiser/items/${id}`, "PATCH", changes),
  orgDelete: (id: string) => request<void>(`/api/organiser/items/${id}`, { method: "DELETE" }),
  orgMakeTasks: (id: string) =>
    request<{ item: OrgItem; made: OrgItem[] }>(`/api/organiser/items/${id}/tasks`, {
      method: "POST",
    }),
  orgRevisions: (id: string) => request<OrgRevision[]>(`/api/organiser/items/${id}/revisions`),
  orgRevision: (id: string, rev: string) =>
    request<OrgRevision>(`/api/organiser/items/${id}/revisions/${rev}`),
  orgRestore: (id: string, rev: string) =>
    request<OrgItem>(`/api/organiser/items/${id}/revisions/${rev}/restore`, { method: "POST" }),
  orgReview: () => request<OrgReview>("/api/organiser/review"),
  orgFinishReview: (focus: string[], notes: string) =>
    request<OrgReview>("/api/organiser/review", { method: "POST", body: json({ focus, notes }) }),
  orgExportUrl: "/api/organiser/export.zip",

  moneySettings: () => request<MoneySettings>("/api/money/settings"),
  moneyUpdateSettings: (body: { gmail_sync?: boolean; assistant_access?: boolean; cas_password?: string }) =>
    request<MoneySettings>("/api/money/settings", { method: "PATCH", body: json(body) }),
  moneySync: () => request<{ queued: boolean }>("/api/money/sync", { method: "POST" }),
  moneyOverview: (month?: string) =>
    request<MoneyOverview>(`/api/money/overview${orgQuery({ month })}`),
  moneyTxns: (q: TxnQuery = {}) =>
    request<TxnPage>(`/api/money/transactions${orgQuery(q)}`),
  moneyTxn: (id: string) => request<MoneyTxn>(`/api/money/transactions/${id}`),
  moneyTxnMatches: (id: string) => request<TxnMatches>(`/api/money/transactions/${id}/matches`),
  moneyScanLinks: () =>
    request<{ transfers: number; card_payments: number; refunds: number; savings: number; self_transfers: number }>("/api/money/links/scan", { method: "POST" }),
  moneyUpdateTxn: (
    id: string,
    body: {
      category?: string;
      for_merchant?: boolean;
      excluded?: boolean;
      note?: string;
      unpair?: boolean;
      pair_with?: string;
      pair_as?: "transfer" | "refund";
      asset_id?: string | null;
    },
  ) =>
    request<{ txn: MoneyTxn; also_changed: number }>(`/api/money/transactions/${id}`, {
      method: "PATCH",
      body: json(body),
    }),
  moneyAddTxn: (body: {
    occurred_on: string;
    amount: string;
    direction: "debit" | "credit";
    merchant: string;
    category?: string;
    account_id?: string;
    note?: string;
  }) => request<MoneyTxn>("/api/money/transactions", { method: "POST", body: json(body) }),
  moneyDeleteTxn: (id: string) =>
    request<void>(`/api/money/transactions/${id}`, { method: "DELETE" }),
  moneyAccounts: () => request<MoneyAccount[]>("/api/money/accounts"),
  moneyRenameAccount: (id: string, name: string) =>
    request<MoneyAccount>(`/api/money/accounts/${id}`, { method: "PATCH", body: json({ name }) }),
  moneySetBudget: (category: string, monthly_paise: number) =>
    request<void>(`/api/money/budgets/${encodeURIComponent(category)}`, {
      method: "PUT",
      body: json({ monthly_paise }),
    }),
  moneyDeleteBudget: (category: string) =>
    request<void>(`/api/money/budgets/${encodeURIComponent(category)}`, { method: "DELETE" }),
  moneySubscriptions: () => request<Subscriptions>("/api/money/subscriptions"),
  moneySaveSubscription: (body: SubscriptionSave) =>
    request<Subscription>("/api/money/subscriptions", { method: "PUT", body: json(body) }),
  moneyForgetSubscription: (key: string) =>
    request<void>(`/api/money/subscriptions/${encodeURIComponent(key)}`, { method: "DELETE" }),
  moneyBalances: () => request<Balances>("/api/money/balances"),
  moneyMergeAccount: (id: string, into_id: string) =>
    request<{ moved: number; matched: number; into: string }>(`/api/money/accounts/${id}/merge`, { method: "POST", body: json({ into_id }) }),
  moneyEditAccount: (id: string, body: AccountEdit) =>
    request<AccountView>(`/api/money/accounts/${id}/details`, { method: "PATCH", body: json(body) }),
  moneySavings: () => request<Savings>("/api/money/savings"),
  moneyAddSaving: (body: AssetIn) =>
    request<Asset>("/api/money/savings", { method: "POST", body: json(body) }),
  moneyEditSaving: (id: string, body: Partial<AssetIn>) =>
    request<Asset>(`/api/money/savings/${id}`, { method: "PATCH", body: json(body) }),
  moneySavingDetails: (id: string) => request<SecretDetails>(`/api/money/savings/${id}/details`),
  moneyLoanDetails: (id: string) => request<SecretDetails>(`/api/money/loans/${id}/details`),
  /** The whole of Money as an Excel file; locked when a password is given. */
  moneyExport: async (password: string): Promise<{ blob: Blob; name: string }> => {
    const resp = await fetch("/api/money/export", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: json({ password: password || null }),
    });
    if (!resp.ok) {
      let detail = resp.statusText;
      try {
        const body = await resp.json();
        if (typeof body.detail === "string") detail = body.detail;
      } catch {
        /* not JSON */
      }
      throw new ApiError(resp.status, detail);
    }
    const name = /filename="([^"]+)"/.exec(resp.headers.get("content-disposition") ?? "")?.[1] ?? "finances.xlsx";
    return { blob: await resp.blob(), name };
  },
  moneyDeleteSaving: (id: string) => request<void>(`/api/money/savings/${id}`, { method: "DELETE" }),
  moneyLoans: () => request<Loans>("/api/money/loans"),
  moneyAddLoan: (body: LoanIn) => request<Loan>("/api/money/loans", { method: "POST", body: json(body) }),
  moneyEditLoan: (id: string, body: Partial<LoanIn> & { status?: "active" | "closed" }) =>
    request<Loan>(`/api/money/loans/${id}`, { method: "PATCH", body: json(body) }),
  moneyDeleteLoan: (id: string) => request<void>(`/api/money/loans/${id}`, { method: "DELETE" }),
  moneyPlan: (q: { months?: number; new_sip_paise?: number; fund_return_bp?: number } = {}) =>
    request<Plan>(`/api/money/plan${orgQuery(q)}`),
  brief: () => request<BriefPage>("/api/brief"),
  briefPlaces: (q: string) => request<{ places: Place[] }>(`/api/brief/places${orgQuery({ q })}`),
  briefSaveSettings: (body: Partial<BriefSettings>) =>
    request<BriefSettings>("/api/brief/settings", { method: "PUT", body: json(body) }),
  briefSendNow: () => request<{ queued: boolean }>("/api/brief/send", { method: "POST" }),
  moneyHealth: () => request<Health>("/api/money/health"),
  moneySaveAssumptions: (body: Partial<Assumptions>) =>
    request<Assumptions>("/api/money/plan/assumptions", { method: "PUT", body: json(body) }),
  moneyAddGoal: (body: { name: string; target_paise: number; by_on: string; saved_paise?: number; asset_ids?: string[] }) =>
    request<{ id: string }>("/api/money/goals", { method: "POST", body: json(body) }),
  moneyEditGoal: (id: string, body: { name?: string; target_paise?: number; by_on?: string; saved_paise?: number; asset_ids?: string[] }) =>
    request<{ id: string }>(`/api/money/goals/${id}`, { method: "PATCH", body: json(body) }),
  moneyDeleteGoal: (id: string) => request<void>(`/api/money/goals/${id}`, { method: "DELETE" }),
  moneySalary: () => request<Salaries>("/api/money/salary"),
  moneyAddSalary: (body: SalaryIn) => request<Salaries>("/api/money/salary", { method: "POST", body: json(body) }),
  moneyEditSalary: (id: string, body: Partial<SalaryIn>) => request<Salaries>(`/api/money/salary/${id}`, { method: "PATCH", body: json(body) }),
  moneyDeleteSalary: (id: string) => request<Salaries>(`/api/money/salary/${id}`, { method: "DELETE" }),
  moneyInvestments: () => request<Investments>("/api/money/investments"),
  moneyStocks: () => request<Stocks>("/api/money/stocks"),
  moneyGrowth: (range: GrowthRange) => request<Growth>(`/api/money/investments/history?range=${range}`),
  moneyFindStock: (q: string) => request<{ items: StockFound[]; price_on: string | null }>(`/api/money/stocks/search${orgQuery({ q })}`),
  moneyAddStock: (body: NewStock) => request<Stock>("/api/money/stocks", { method: "POST", body: json(body) }),
  moneyEditStock: (id: string, body: StockEdit) => request<Stock>(`/api/money/stocks/${id}`, { method: "PATCH", body: json(body) }),
  moneyDeleteStock: (id: string) => request<void>(`/api/money/stocks/${id}`, { method: "DELETE" }),
  moneyBanks: () => request<{ banks: string[] }>("/api/money/banks"),
  moneyAddAccount: (body: NewAccount) => request<{ id: string; label: string; moved?: number }>("/api/money/accounts", { method: "POST", body: json(body) }),
  moneySuggestions: () => request<Suggestions>("/api/money/suggestions"),
  moneyAnswerSuggestion: (key: string, choice: string, amount_paise?: number, text?: string) =>
    request<Suggestions & { done: string }>("/api/money/suggestions/answer", { method: "POST", body: json({ key, choice, amount_paise, text }) }),
  moneyDaily: (month?: string) => request<Daily>(`/api/money/daily${orgQuery({ month })}`),
  moneySetItems: (id: string, body: { text?: string; kind?: string; items?: Omit<ReadItem, "kind_label">[] }) =>
    request<{ items: BoughtItem[] }>(`/api/money/transactions/${id}/items`, { method: "PUT", body: json(body) }),
  moneyUpdateItem: (id: string, kind: string) =>
    request<BoughtItem>(`/api/money/items/${id}`, { method: "PATCH", body: json({ kind }) }),
  moneyAcceptSuggestions: () => request<Suggestions & { accepted: number }>("/api/money/suggestions/accept-all", { method: "POST" }),
  moneyOrdersToConfirm: () => request<{ items: OrderToConfirm[] }>("/api/money/orders/to-confirm"),
  moneyOrderPaid: (id: string, body: OrderPaid) =>
    request<{ items: OrderToConfirm[] }>(`/api/money/orders/${id}/paid`, { method: "POST", body: json(body) }),
  moneyRemoveMailbox: (id: string) => request<void>(`/api/money/mailboxes/${id}`, { method: "DELETE" }),
  advice: () => request<Advice>("/api/advice"),
  adviceState: (key: string, action: AdviceAction, days?: number) =>
    request<Advice>("/api/advice/state", { method: "POST", body: json({ key, action, days }) }),
  moneyReadOrderShot: (file: File, txnId?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (txnId) form.append("txn_id", txnId);
    return request<OrderReading>("/api/money/orders/screenshot", { method: "POST", body: form });
  },
  moneyReadScreenshot: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<ScreenshotReading>("/api/money/screenshots/read", { method: "POST", body: form });
  },
  moneySaveScreenshots: (body: ScreenshotSave) =>
    request<{ added: number; matched: number; skipped: number; account_label: string | null }>("/api/money/screenshots/save", { method: "POST", body: json(body) }),
  moneyReadHistory: (months: number) => request<{ queued: boolean; months: number }>("/api/money/sync/history", { method: "POST", body: json({ months }) }),
  moneyImports: () => request<MoneyImport[]>("/api/money/imports"),
  moneyTracking: () => request<Tracking>("/api/money/tracking"),
  moneyMailStatements: () => request<MailStatement[]>("/api/money/mail-statements"),
  moneyImportMailStatement: (id: string, body: { password: string; replace: boolean; account_id?: string }) =>
    request<MoneyImport>(`/api/money/mail-statements/${id}/import`, { method: "POST", body: json(body) }),
  moneyDismissMailStatement: (id: string) => request<void>(`/api/money/mail-statements/${id}/dismiss`, { method: "POST" }),
  moneyImport: (file: File, opts: { password?: string; remember?: boolean; account_id?: string; replace?: boolean } = {}) => {
    const form = new FormData();
    form.append("file", file);
    if (opts.password) form.append("password", opts.password);
    form.append("remember_password", String(opts.remember ?? true));
    if (opts.account_id) form.append("account_id", opts.account_id);
    if (opts.replace) form.append("replace", "true");
    return request<MoneyImport>("/api/money/imports", { method: "POST", body: form });
  },
  moneyDeleteAll: () =>
    request<void>("/api/money/delete-all", { method: "POST", body: json({ confirm: "delete" }) }),
  authConfig: () => request<AuthConfig>("/auth/config"),
  accessState: () => request<AccessState>("/auth/access-request"),
  requestAccess: (note: string) =>
    request<AccessState>("/auth/access-request", {
      method: "POST",
      body: json({ note: note.trim() || null }),
    }),
  accountRecovery: (action: "recover" | "new") =>
    request<AccessState>("/auth/account-recovery", {
      method: "POST",
      body: json({ action }),
    }),
  devLogin: (email: string, token: string) =>
    request<User>("/auth/dev-login", { method: "POST", body: json({ email, token }) }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  me: () => request<User>("/api/me"),
  nameAssistant: (assistant_name: string) =>
    request<User>("/api/me", { method: "PATCH", body: json({ assistant_name }) }),
  googleAccess: () => request<GoogleAccess>("/api/me/google"),
  disconnectGoogle: () => request<void>("/api/me/google", { method: "DELETE" }),
  sessions: () => request<SessionInfo[]>("/api/me/sessions"),
  signOutSession: (id: string) => request<void>(`/api/me/sessions/${id}`, { method: "DELETE" }),
  signOutOthers: () => request<void>("/api/me/sessions/sign-out-others", { method: "POST" }),
  updateMe: (changes: {
    timezone?: string;
    phone?: string;
    share_usage?: boolean;
    tour_done?: boolean;
  }) =>
    request<User>("/api/me", { method: "PATCH", body: json(changes) }),
  profiles: () => request<Profiles>("/api/profiles"),
  onboard: (answers: Partial<OnboardingAnswers>) =>
    request<{ me: User; chat_id: string | null; routines: number }>("/api/me/onboarding", {
      method: "POST",
      body: json(answers),
    }),
  suggestions: () => request<string[]>("/api/me/suggestions"),
  notifications: () => request<{ items: Notice[]; unread: number }>("/api/notifications"),
  widgets: () => request<Widget[]>("/api/widgets"),
  /** A web search whose results show in Nexus, optionally kept to some sites. */
  webSearch: (q: string, sites: string[] = []) =>
    request<WebResult[]>(
      `/api/web/search?${new URLSearchParams([["q", q], ...sites.map((s) => ["sites", s] as [string, string])])}`,
    ),
  /** A page as clean text for the reading view. */
  webRead: (url: string) => request<WebPage>(`/api/web/read?${new URLSearchParams({ url })}`),
  /** The widgets put away, newest first. */
  widgetLibrary: () => request<Widget[]>("/api/widgets/library"),
  addWidget: (body: WidgetInput) => request<Widget>("/api/widgets", { method: "POST", body: json(body) }),
  widgetAction: (id: string, action: WidgetAction, minutes?: number) =>
    request<Widget | null>(`/api/widgets/${id}/action`, { method: "POST", body: json({ action, minutes }) }),
  editWidget: (
    id: string,
    body: {
      title?: string;
      text?: string;
      slot?: number;
      pos?: WidgetPos;
      dock?: boolean;
      size?: WidgetSize;
      reset_size?: boolean;
      engine?: SearchEngine;
      tune?: TuneId;
      time?: string;
      days?: string | string[] | null;
    },
  ) =>
    request<Widget>(`/api/widgets/${id}`, { method: "PATCH", body: json(body) }),
  /** Mark these notices read, or every one when ids is left out. */
  screen: () => request<ScreenState>("/api/screen"),
  setScreenMode: (mode: ScreenMode) =>
    request<ScreenState>("/api/screen/mode", { method: "PUT", body: json({ mode }) }),
  closeOnScreen: (kind: "file" | "widget", id: string) =>
    request<ScreenState>("/api/screen/close", { method: "POST", body: json({ kind, id }) }),
  widgetBridge: (id: string, call: BridgeCall, args: Record<string, unknown>) =>
    request<{ result: unknown }>(`/api/widgets/${id}/bridge`, { method: "POST", body: json({ call, args }) }),
  approveWidget: (id: string, hosts: string[], data: string[], actions: string[] = []) =>
    request<Widget>(`/api/widgets/${id}/approve`, { method: "POST", body: json({ hosts, data, actions }) }),
  readNotifications: (ids?: string[]) =>
    request<void>("/api/notifications/read", { method: "POST", body: json({ ids: ids ?? null }) }),
  acceptTerms: (version: string) =>
    request<User>("/api/me/terms", { method: "POST", body: json({ version }) }),
  deleteAccount: (confirm: string) =>
    request<void>("/api/me/delete", { method: "POST", body: json({ confirm }) }),

  pushState: () =>
    request<{ enabled: boolean; public_key: string | null; devices: number }>("/api/push"),
  pushSubscribe: (sub: { endpoint: string; keys: { p256dh: string; auth: string } }) =>
    request<void>("/api/push/subscriptions", { method: "POST", body: json(sub) }),
  pushUnsubscribe: (endpoint: string) =>
    request<void>("/api/push/unsubscribe", { method: "POST", body: json({ endpoint }) }),
  pushTest: () => request<{ sent: number }>("/api/push/test", { method: "POST" }),
  sendFeedback: (body: { kind: FeedbackKind; message: string; page: string; screenshots?: File[] }) => {
    const form = new FormData();
    form.append("kind", body.kind);
    form.append("message", body.message);
    form.append("page", body.page);
    for (const shot of body.screenshots ?? []) form.append("screenshots", shot);
    return request<FeedbackItem>("/api/feedback", { method: "POST", body: form });
  },
  feedbackShotUrl: (id: string, n: number) => `/api/admin/feedback/${id}/screenshots/${n}`,
  feedbackList: (openOnly = false) =>
    request<FeedbackItem[]>(`/api/admin/feedback${openOnly ? "?open_only=true" : ""}`),
  markFeedback: (id: string, done: boolean) =>
    request<FeedbackItem>(`/api/admin/feedback/${id}/done?done=${done}`, { method: "POST" }),

  routines: () => request<Routine[]>("/api/routines"),
  createRoutine: (body: RoutineInput) =>
    request<Routine>("/api/routines", { method: "POST", body: json(body) }),
  updateRoutine: (id: string, changes: Partial<RoutineInput> & { delivery?: Channel[] }) =>
    request<Routine>(`/api/routines/${id}`, { method: "PATCH", body: json(changes) }),
  deleteRoutine: (id: string) => request<void>(`/api/routines/${id}`, { method: "DELETE" }),
  runRoutine: (id: string) =>
    request<{ queued: boolean; chat_id: string | null }>(`/api/routines/${id}/run`, {
      method: "POST",
    }),
  routineRuns: (id: string) => request<RoutineRun[]>(`/api/routines/${id}/runs`),
  previewSchedule: (schedule: string, timezone?: string) =>
    request<SchedulePreview>("/api/routines/preview", {
      method: "POST",
      body: json({ schedule, timezone }),
    }),

  chats: () => request<Chat[]>("/api/chats"),
  trash: () => request<TrashItem[]>("/api/trash"),
  restoreTrash: (id: string) =>
    request<TrashItem>(`/api/trash/${id}/restore`, { method: "POST" }),
  permanentlyDeleteTrash: (id: string) =>
    request<{ queued: boolean }>(`/api/trash/${id}`, { method: "DELETE" }),
  createChat: () => request<Chat>("/api/chats", { method: "POST", body: json({}) }),
  /** A copy of the chat so far (up to `until`, a message id) as a new chat. */
  forkChat: (id: string, until?: string) =>
    request<Chat>(`/api/chats/${id}/fork`, { method: "POST", body: json(until ? { until } : {}) }),
  renameChat: (id: string, title: string) =>
    request<Chat>(`/api/chats/${id}`, { method: "PATCH", body: json({ title }) }),
  deleteChat: (id: string) => request<void>(`/api/chats/${id}`, { method: "DELETE" }),
  messages: (chatId: string) => request<Message[]>(`/api/chats/${chatId}/messages`),
  send: (chatId: string, content: string, file_ids: string[] = []) =>
    request<{ chat: Chat; user_message: Message; assistant_message: Message }>(
      `/api/chats/${chatId}/messages`,
      { method: "POST", body: json({ content, file_ids }) },
    ),

  files: () => request<FileItem[]>("/api/files"),
  file: (id: string) => request<FileItem>(`/api/files/${id}`),
  link: (id: string) => request<LinkItem>(`/api/links/${id}`),
  projects: () => request<Project[]>("/api/projects"),
  deleteProject: (id: string) =>
    request<{ queued: boolean }>(`/api/projects/${id}`, { method: "DELETE" }),
  links: (q?: string) => request<LinkItem[]>(`/api/links${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  addLink: (url: string, title?: string, note?: string) =>
    request<LinkItem>("/api/links", { method: "POST", body: json({ url, title: title || null, note: note || null }) }),
  editLink: (id: string, changes: { title?: string; note?: string }) =>
    request<LinkItem>(`/api/links/${id}`, { method: "PATCH", body: json(changes) }),
  refreshLink: (id: string) => request<LinkItem>(`/api/links/${id}/refresh`, { method: "POST" }),
  deleteLink: (id: string) => request<void>(`/api/links/${id}`, { method: "DELETE" }),
  apps: () => request<AppItem[]>("/api/apps"),
  appAction: (id: string, action: "stop" | "restart" | "redeploy") =>
    request<AppItem>(`/api/apps/${id}/${action}`, { method: "POST" }),
  setAppInternet: (id: string, internet: boolean) =>
    request<AppItem>(`/api/apps/${id}`, { method: "PATCH", body: json({ internet }) }),
  setAppPublic: (id: string, isPublic: boolean) =>
    request<AppItem>(`/api/apps/${id}/visibility`, { method: "POST", body: json({ public: isPublic }) }),
  deleteApp: (id: string) => request<void>(`/api/apps/${id}`, { method: "DELETE" }),
  uploadFile: (file: File, chatId?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (chatId) form.append("chat_id", chatId);
    return request<FileItem>("/api/files", { method: "POST", body: form });
  },
  fileText: (id: string, offset = 0) =>
    request<{ text: string; offset: number; total_chars: number }>(
      `/api/files/${id}/text?offset=${offset}`,
    ),
  telegram: () => request<TelegramStatus>("/api/telegram"),
  telegramLink: () =>
    request<{ code: string; url: string | null; expires_at: string }>("/api/telegram/link", { method: "POST" }),
  telegramUnlink: () => request<void>("/api/telegram/link", { method: "DELETE" }),
  /** Search across everything; `limit` is per kind. */
  find: (
    q: string,
    opts: { kinds?: FindKind[]; limit?: number; meaning?: boolean; signal?: AbortSignal } = {},
  ) => {
    const params = new URLSearchParams({ q });
    for (const k of opts.kinds ?? []) params.append("kinds", k);
    if (opts.limit) params.set("limit", String(opts.limit));
    if (opts.meaning === false) params.set("meaning", "false");
    return request<FindResponse>(`/api/find?${params}`, { signal: opts.signal });
  },
  searchFiles: (q: string) => request<SearchHit[]>(`/api/files/search?q=${encodeURIComponent(q)}`),
  renameFile: (id: string, name: string) =>
    request<FileItem>(`/api/files/${id}`, { method: "PATCH", body: json({ name }) }),
  /** Moves the file and its other versions; null takes them out of any folder. */
  moveFile: (id: string, folderId: string | null) =>
    request<FileItem>(`/api/files/${id}`, { method: "PATCH", body: json({ folder_id: folderId }) }),
  folders: () => request<Folder[]>("/api/folders"),
  addFolder: (name: string) => request<Folder>("/api/folders", { method: "POST", body: json({ name }) }),
  renameFolder: (id: string, name: string) =>
    request<Folder>(`/api/folders/${id}`, { method: "PATCH", body: json({ name }) }),
  deleteFolder: (id: string) => request<void>(`/api/folders/${id}`, { method: "DELETE" }),
  retryFile: (id: string) => request<FileItem>(`/api/files/${id}/retry`, { method: "POST" }),
  deleteFile: (id: string) => request<void>(`/api/files/${id}`, { method: "DELETE" }),
  fileUrl: (id: string, inline = false) => `/api/files/${id}/content${inline ? "?inline=true" : ""}`,

  memories: (q?: string) =>
    request<Memory[]>(`/api/memories${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  addMemory: (content: string, kind: MemoryKind) =>
    request<Memory>("/api/memories", { method: "POST", body: json({ content, kind }) }),
  updateMemory: (id: string, changes: { content?: string; kind?: MemoryKind; pinned?: boolean }) =>
    request<Memory>(`/api/memories/${id}`, { method: "PATCH", body: json(changes) }),
  deleteMemory: (id: string, rebuild = false) =>
    request<void>(`/api/memories/${id}${rebuild ? "?rebuild=true" : ""}`, { method: "DELETE" }),
  memoryEvents: (id: string) => request<MemoryEvent[]>(`/api/memories/${id}/events`),
  knowledgeGraph: () => request<KnowledgeGraph>("/api/memories/graph"),
  universe: () => request<Universe>("/api/memories/universe"),
  questionCards: () => request<GraphCard[]>("/api/questions"),
  answerCard: (id: string, answer: CardAnswer) =>
    request<{ note: string; entities: string[]; memories: number }>(`/api/questions/${id}/answer`, {
      method: "POST",
      body: json({ answer }),
    }),
  skipCard: (id: string) => request<void>(`/api/questions/${id}/skip`, { method: "POST" }),
  imports: () => request<ChatImportItem[]>("/api/imports"),
  uploadImport: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<ChatImportItem>("/api/imports", { method: "POST", body: form });
  },
  deleteImport: (id: string) => request<void>(`/api/imports/${id}`, { method: "DELETE" }),
  deleteEntity: (id: string) => request<void>(`/api/memories/entities/${id}`, { method: "DELETE" }),
  forgetEverything: () => request<void>("/api/memories", { method: "DELETE" }),
  profile: () => request<Profile>("/api/memories/profile"),
  setMemoryEnabled: (memory_enabled: boolean) =>
    request<Profile>("/api/memories/profile", { method: "PATCH", body: json({ memory_enabled }) }),
  rebuildProfile: () => request<void>("/api/memories/profile/rebuild", { method: "POST" }),

  skills: (q?: string) =>
    request<Skill[]>(`/api/skills${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  skill: (id: string) => request<SkillDetail>(`/api/skills/${id}`),
  createSkill: (content: string, scope: "personal" | "domain" = "personal") =>
    request<SkillDetail>("/api/skills", { method: "POST", body: json({ content, scope }) }),
  importSkill: (file: File, scope: "personal" | "domain" = "personal") => {
    const form = new FormData();
    form.append("file", file);
    form.append("scope", scope);
    return request<SkillDetail>("/api/skills/import", { method: "POST", body: form });
  },
  editSkill: (id: string, content: string, files?: Record<string, string>, note?: string) =>
    request<SkillDetail>(`/api/skills/${id}`, {
      method: "PUT",
      body: json({ content, ...(files ? { files } : {}), ...(note ? { note } : {}) }),
    }),
  setSkillStatus: (id: string, status: "active" | "disabled") =>
    request<Skill>(`/api/skills/${id}/status`, { method: "POST", body: json({ status }) }),
  skillVersions: (id: string) => request<SkillVersion[]>(`/api/skills/${id}/versions`),
  rollbackSkill: (id: string, version: number) =>
    request<SkillDetail>(`/api/skills/${id}/rollback`, {
      method: "POST",
      body: json({ version }),
    }),
  shareSkill: (id: string) => request<SkillDetail>(`/api/skills/${id}/share`, { method: "POST" }),
  deleteSkill: (id: string) => request<void>(`/api/skills/${id}`, { method: "DELETE" }),

  instructions: () => request<Instructions>("/api/instructions"),
  setInstructions: (layer: "user" | "global" | `roles/${string}`, content: string) =>
    request<void>(`/api/instructions/${layer}`, { method: "PUT", body: json({ content }) }),

  tools: () => request<ToolEntry[]>("/api/tools"),
  systems: (params: { chat_id?: string; archived?: boolean } = {}) => {
    const q = new URLSearchParams();
    if (params.chat_id) q.set("chat_id", params.chat_id);
    if (params.archived) q.set("archived", "true");
    return request<AgentSystem[]>(`/api/systems${q.size ? `?${q}` : ""}`);
  },
  system: (id: string) => request<SystemDetail>(`/api/systems/${id}`),
  runSystem: (id: string, body: { request: string; chat_id?: string; budget_usd?: number }) =>
    request<AgentSystem>(`/api/systems/${id}/runs`, { method: "POST", body: json(body) }),
  keepAsTemplate: (id: string, name?: string) =>
    request<AgentSystem>(`/api/systems/${id}/template`, {
      method: "POST",
      body: json(name ? { name } : {}),
    }),
  deleteSystem: (id: string) => request<void>(`/api/systems/${id}`, { method: "DELETE" }),
  run: (id: string) => request<RunDetail>(`/api/runs/${id}`),
  controlRun: (id: string, action: "pause" | "resume" | "cancel") =>
    request<Run>(`/api/runs/${id}/${action}`, { method: "POST" }),
  approvals: (chatId?: string) =>
    request<Approval[]>(`/api/approvals${chatId ? `?chat_id=${chatId}` : ""}`),
  decide: (
    id: string,
    decision: "approve" | "reject",
    extra: { args?: Record<string, unknown>; note?: string } = {},
  ) =>
    request<Approval>(`/api/approvals/${id}`, {
      method: "POST",
      body: json({ decision, ...extra }),
    }),

  users: () => request<User[]>("/api/admin/users"),
  grants: (userId: string) => request<Grants>(`/api/admin/users/${userId}/grants`),
  setGrants: (userId: string, capabilities: string[]) =>
    request<Grants>(`/api/admin/users/${userId}/grants`, {
      method: "PUT",
      body: json({ capabilities }),
    }),
  invite: (email: string, role: Role, budget_usd?: string) =>
    request<User>("/api/admin/users", {
      method: "POST",
      body: json({ email, role, ...(budget_usd ? { budget_usd } : {}) }),
    }),
  retryInviteNotice: (id: string) =>
    request<void>(`/api/admin/users/${id}/invite-email`, { method: "POST" }),
  revokeInvite: (id: string) => request<void>(`/api/admin/users/${id}`, { method: "DELETE" }),
  accessRequests: (status: "pending" | "decided" = "pending") =>
    request<AccessRequestList>(`/api/admin/access-requests?status=${status}`),
  approveAccess: (id: string, role: Role, budget_usd?: string) =>
    request<{ request: AccessRequest; user: User }>(`/api/admin/access-requests/${id}/approve`, {
      method: "POST",
      body: json({ role, ...(budget_usd ? { budget_usd } : {}) }),
    }),
  denyAccess: (id: string) =>
    request<AccessRequest>(`/api/admin/access-requests/${id}/deny`, { method: "POST" }),
  accountRecoveryRequests: (status: "pending" | "decided" = "pending") =>
    request<AccountRecoveryList>(`/api/admin/account-recovery?status=${status}`),
  approveAccountRecovery: (id: string) =>
    request<AccountRecoveryRequest>(`/api/admin/account-recovery/${id}/approve`, { method: "POST" }),
  denyAccountRecovery: (id: string) =>
    request<AccountRecoveryRequest>(`/api/admin/account-recovery/${id}/deny`, { method: "POST" }),
  signOutUser: (id: string) =>
    request<void>(`/api/admin/users/${id}/sign-out`, { method: "POST" }),
  resetUser: (id: string, confirm: string) =>
    request<{ status: string }>(`/api/admin/users/${id}/reset`, {
      method: "POST",
      body: json({ confirm }),
    }),
  systemStatus: (userIds: string[] = []) =>
    request<OpsStatus>(`/api/admin/status${userIds.length ? `?${forUsers(userIds)}` : ""}`),
  usage: (days = 7, userIds: string[] = []) => {
    const q = forUsers(userIds);
    q.set("days", String(days));
    return request<Usage>(`/api/admin/usage?${q}`);
  },
  audit: (params: { action?: string; before?: string; userIds?: string[] } = {}) => {
    const q = forUsers(params.userIds ?? []);
    if (params.action) q.set("action", params.action);
    if (params.before) q.set("before", params.before);
    return request<{ entries: AuditEntry[]; actions: string[] }>(
      `/api/admin/audit${q.size ? `?${q}` : ""}`,
    );
  },
  backups: () => request<Backup[]>("/api/admin/backups"),
  monitoring: (minutes: number) => request<Monitoring>(`/api/admin/monitoring?minutes=${minutes}`),
  adminSummary: () => request<{ alerts: number; proposals: number }>("/api/admin/summary"),
  alerts: () => request<Alerts>("/api/admin/alerts"),
  llmUsage: (query: LlmQuery) => {
    const q = forUsers(query.userIds);
    if (query.since) q.set("since", query.since);
    if (query.until) q.set("until", query.until);
    for (const m of query.models) q.append("model", m);
    for (const p of query.purposes) q.append("purpose", p);
    return request<LlmUsage>(`/api/admin/llm?${q}`);
  },
  improvements: (status?: string) =>
    request<Improvements>(`/api/admin/improvements${status ? `?status=${status}` : ""}`),
  reviewNow: () => request<Review>("/api/admin/improvements/review", { method: "POST" }),
  recall: () => request<RecallSummary>("/api/admin/recall"),
  runRecallCheck: () => request<RecallCheck>("/api/admin/recall/run", { method: "POST" }),
  resetRecallQuestions: () => request<void>("/api/admin/recall/questions", { method: "DELETE" }),
  graphAdmin: () => request<GraphAdmin>("/api/admin/graph"),
  runGraph: (what: "extract" | "rearrange") =>
    request<{ queued: string }>("/api/admin/graph/run", { method: "POST", body: json({ what }) }),
  graphRebuild: (confirm: string) =>
    request<{ relations: number; entities: number }>("/api/admin/graph/rebuild", {
      method: "POST",
      body: json({ confirm }),
    }),
  approveProposal: (id: string, note?: string) =>
    request<Proposal>(`/api/admin/improvements/${id}/approve`, { method: "POST", body: json({ note: note || null }) }),
  denyProposal: (id: string, note?: string) =>
    request<Proposal>(`/api/admin/improvements/${id}/deny`, { method: "POST", body: json({ note: note || null }) }),
  revertProposal: (id: string) =>
    request<Proposal>(`/api/admin/improvements/${id}/revert`, { method: "POST" }),
  backupNow: () => request<Backup>("/api/admin/backups", { method: "POST" }),
  config: () => request<ConfigView>("/api/admin/config"),
  /** A value per setting, or null to go back to the .env value. */
  saveConfig: (values: Record<string, ConfigValue | null>) =>
    request<ConfigView>("/api/admin/config", { method: "PUT", body: json({ values }) }),
  /** Sends the signed-in owner a test email with the saved Email settings. */
  testEmail: () => request<{ to: string; from: string }>("/api/admin/config/test-email", { method: "POST" }),
  setMaintenance: (enabled: boolean, message?: string) =>
    request<{ enabled: boolean; message: string | null }>("/api/admin/maintenance", {
      method: "PUT",
      body: json({ enabled, message }),
    }),
  updateUser: (id: string, changes: { role?: Role; status?: UserStatus; budget_usd?: string }) =>
    request<User>(`/api/admin/users/${id}`, { method: "PATCH", body: json(changes) }),
};
