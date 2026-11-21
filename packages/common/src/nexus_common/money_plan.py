"""Money's planning maths: what savings are worth, what loans have left, where net worth is
heading and what a goal needs each month. Pure functions on plain values, no database; amounts
are whole paise and rates are basis points a year (7.1% = 710).

The forecast is deliberately simple and explainable rather than clever:

- Cash grows by income minus spending each month. Both start from the last three full months
  (or the person's own figures) and grow with the assumed raise and inflation.
- EMIs come off spending as each loan ends, so the forecast shows the month money frees up.
- Deposits, PPF, EPF and NPS follow their own terms; funds grow at the assumed return.
- The band around the line is one standard deviation of how much the monthly surplus has
  moved about, growing with the square root of time, with the low and high fund returns: about
  two months in three should land inside it if the next months look like the last ones.
"""

import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date

from nexus_common.money import add_months

ASSET_KINDS = {
    "fd": "Fixed deposit",
    "rd": "Recurring deposit",
    "ppf": "PPF",
    "epf": "EPF",
    "nps": "NPS",
    "gold": "Gold",
    "other": "Other savings",
}
# Government-set rates as of November 2026: PPF 7.1% (Oct-Dec 2026 quarter), EPF 8.25%
# (FY 2025-26). The person can change them per account.
DEFAULT_RATE_BP = {"ppf": 710, "epf": 825}
# Kinds whose amount is a balance that grows, rather than a deposit with fixed terms.
BALANCE_KINDS = ("ppf", "epf", "nps", "gold", "other")
# Money for these comes out of the bank account (EPF comes out of salary before it arrives).
CASH_FUNDED = ("rd", "ppf", "nps", "other")
LIQUID_KINDS = ("fd",)

LOAN_KINDS = {
    "card_emi": "Card EMI",
    "personal": "Personal loan",
    "home": "Home loan",
    "vehicle": "Vehicle loan",
    "education": "Education loan",
    "other": "Other loan",
}


def _years(a: date, b: date) -> float:
    return max((b - a).days, 0) / 365.0


def _months_between(a: date, b: date) -> int:
    """Whole months from a to b (0 when b is before a)."""
    n = (b.year - a.year) * 12 + (b.month - a.month) - (b.day < a.day)
    return max(n, 0)


def _monthly(rate_bp: int) -> float:
    """The monthly rate with the same effect over a year as rate_bp compounded yearly."""
    return (1 + rate_bp / 10000) ** (1 / 12) - 1


# --- Savings ------------------------------------------------------------------------------------


@dataclass
class Asset:
    kind: str
    amount_paise: int
    rate_bp: int = 0
    start_on: date | None = None
    maturity_on: date | None = None
    balance_on: date | None = None
    contribution_paise: int = 0
    payout: bool = False
    status: str = "active"


def _fd_value(a: Asset, on: date) -> int:
    if a.payout or not a.start_on:
        return a.amount_paise
    end = min(on, a.maturity_on) if a.maturity_on else on
    # Indian banks compound fixed deposits quarterly.
    return round(a.amount_paise * (1 + a.rate_bp / 40000) ** (4 * _years(a.start_on, end)))


def _rd_value(a: Asset, on: date) -> int:
    """Each monthly instalment earns quarterly-compounded interest from the day it goes in."""
    if not a.start_on:
        return 0
    end = min(on, a.maturity_on) if a.maturity_on else on
    total = 0.0
    k = 0
    while True:
        paid = add_months(a.start_on, k)
        if paid > end or (a.maturity_on and paid >= a.maturity_on):
            break
        total += a.amount_paise * (1 + a.rate_bp / 40000) ** (4 * _years(paid, end))
        k += 1
        if k > 600:
            break
    return round(total)


def _balance_value(a: Asset, on: date) -> int:
    """A balance that earns interest and takes contributions: monthly for EPF and NPS, each
    April for PPF (the yearly deposit is best made by 5 April)."""
    since = a.balance_on or a.start_on or on
    value = float(a.amount_paise)
    r = _monthly(a.rate_bp)
    months = _months_between(since, on)
    if a.maturity_on:
        months = min(months, _months_between(since, a.maturity_on))
    for i in range(1, months + 1):
        value *= 1 + r
        month = add_months(since, i)
        if a.kind == "ppf":
            if month.month == 4:
                value += a.contribution_paise
        else:
            value += a.contribution_paise
    return round(value)


def asset_value(a: Asset, on: date) -> int:
    """What a saving is worth on a day, from its terms."""
    if a.kind == "fd":
        return _fd_value(a, on)
    if a.kind == "rd":
        return _rd_value(a, on)
    return _balance_value(a, on)


def maturity_value(a: Asset) -> int | None:
    return asset_value(a, a.maturity_on) if a.maturity_on else None


def monthly_contribution(a: Asset) -> int:
    """What the saving takes each month, for the forecast."""
    if a.status != "active":
        return 0
    if a.kind == "rd":
        return a.amount_paise
    if a.kind == "ppf":
        return a.contribution_paise // 12
    if a.kind in BALANCE_KINDS:
        return a.contribution_paise
    return 0


# --- Loans --------------------------------------------------------------------------------------


def emi_for(principal_paise: int, rate_bp: int, months: int) -> int:
    """The standard reducing-balance instalment; a no-cost EMI (0%) splits the amount evenly."""
    if months <= 0:
        return principal_paise
    r = rate_bp / 120000
    if r == 0:
        return math.ceil(principal_paise / months)
    f = (1 + r) ** months
    return round(principal_paise * r * f / (f - 1))


@dataclass
class Loan:
    principal_paise: int
    rate_bp: int
    months: int
    emi_paise: int
    first_due_on: date
    status: str = "active"


def loan_state(loan: Loan, on: date) -> dict:
    """How far through a loan is on a day: instalments paid and left, what's still owed,
    the next due date and when it ends."""
    ends_on = add_months(loan.first_due_on, loan.months - 1)
    paid = 0 if on < loan.first_due_on else min(_months_between(loan.first_due_on, on) + 1,
                                                loan.months)  # fmt: skip
    if loan.status != "active":
        paid = loan.months
    r = loan.rate_bp / 120000
    owed = float(loan.principal_paise)
    interest_paid = 0.0
    for _ in range(paid):
        interest = owed * r
        interest_paid += interest
        owed = max(owed + interest - loan.emi_paise, 0.0)
    left = loan.months - paid
    total_interest = max(loan.emi_paise * loan.months - loan.principal_paise, 0)
    return {
        "paid": paid,
        "left": left,
        "outstanding_paise": round(owed) if left else 0,
        "next_due_on": add_months(loan.first_due_on, paid) if left else None,
        "ends_on": ends_on,
        "total_interest_paise": total_interest,
        "interest_left_paise": max(total_interest - round(interest_paid), 0) if left else 0,
        "done": left == 0,
    }


def emi_in_month(loan: Loan, month_start: date) -> int:
    """The instalment due in the month starting month_start (0 once the loan has ended)."""
    if loan.status != "active":
        return 0
    k = _months_between(date(loan.first_due_on.year, loan.first_due_on.month, 1), month_start)
    if month_start < date(loan.first_due_on.year, loan.first_due_on.month, 1):
        return 0
    return loan.emi_paise if k < loan.months else 0


# --- Goals and investing ------------------------------------------------------------------------


def future_value_monthly(monthly_paise: int, rate_bp: int, months: int) -> int:
    """What putting the same amount in at the start of each month is worth after `months`."""
    r = _monthly(rate_bp)
    if r == 0:
        return monthly_paise * months
    return round(monthly_paise * ((1 + r) ** months - 1) / r * (1 + r))


def monthly_needed(target_paise: int, have_paise: int, rate_bp: int, months: int) -> int:
    """How much to put away each month to reach the target, counting growth on what's saved."""
    if months <= 0:
        return max(target_paise - have_paise, 0)
    r = _monthly(rate_bp)
    grown = have_paise * (1 + r) ** months
    gap = target_paise - grown
    if gap <= 0:
        return 0
    if r == 0:
        return math.ceil(gap / months)
    return math.ceil(gap * r / (((1 + r) ** months - 1) * (1 + r)))


def goal_rate_bp(months: int, fund_return_bp: int) -> int:
    """Money needed within three years belongs somewhere safe (about a deposit's rate);
    beyond that, an equity fund's expected return is fair to assume."""
    return 650 if months < 36 else fund_return_bp


# --- Forecast -----------------------------------------------------------------------------------


@dataclass
class Assumptions:
    """The forecast's inputs. None means "work it out from the last three months"."""

    income_paise: int | None = None
    spend_paise: int | None = None
    # Raise a year, inflation on spending, the expected fund return and its low and high.
    income_growth_bp: int = 500
    inflation_bp: int = 500
    fund_return_bp: int = 1000
    fund_low_bp: int = 600
    fund_high_bp: int = 1300
    # A new monthly investment to try out ("what if I start a ₹10,000 SIP?").
    new_sip_paise: int = 0
    emergency_months: int = 6
    # The share of income to keep, and the cover the safety checks look for. None means
    # "not told yet", which the checks say rather than guess.
    save_target_bp: int = 2000
    health_cover_paise: int | None = None
    life_cover_paise: int | None = None
    dependants: bool | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "Assumptions":
        known = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class History:
    """The last full months, newest last: income, spending and money invested."""

    income: list[int] = field(default_factory=list)
    spend: list[int] = field(default_factory=list)
    invested: list[int] = field(default_factory=list)
    essentials: list[int] = field(default_factory=list)


def recent(values: list[int], n: int = 3) -> int:
    """The mean of the last `n` values (the last three full months), 0 when there are none."""
    tail = values[-n:]
    return round(statistics.mean(tail)) if tail else 0


def forecast(
    *,
    today: date,
    months: int,
    cash_paise: int,
    funds_paise: int,
    assets: list[Asset],
    loans: list[Loan],
    history: History,
    assume: Assumptions,
) -> dict:
    """Month-by-month net worth for the next `months`, with a likely range.

    cash_paise is what's in the bank less what's owed on cards; funds_paise is the mutual
    funds' value today. Returns the starting point, the inputs used, and one point a month."""
    income = assume.income_paise if assume.income_paise is not None else recent(history.income)
    spend_all = assume.spend_paise if assume.spend_paise is not None else recent(history.spend)
    this_month = date(today.year, today.month, 1)
    emis_now = sum(emi_in_month(loan, this_month) for loan in loans)
    # Spending without EMIs, so each loan's end shows up as money freed.
    base_spend = max(spend_all - emis_now, 0)
    contributions = sum(monthly_contribution(a) for a in assets if a.kind in CASH_FUNDED)
    # Money already going into funds each month, less what tracked monthly savings (an RD, NPS)
    # explain. PPF goes in once a year, so it's rarely in the last three months.
    monthly_debits = sum(
        monthly_contribution(a) for a in assets if a.kind in CASH_FUNDED and a.kind != "ppf"
    )
    existing_sip = max(recent(history.invested) - monthly_debits, 0)
    surpluses = [
        i - s - v for i, s, v in zip(history.income, history.spend, history.invested, strict=True)
    ]
    sigma = statistics.pstdev(surpluses[-6:]) if len(surpluses) >= 2 else 0.0

    g_income = _monthly(assume.income_growth_bp)
    g_spend = _monthly(assume.inflation_bp)
    rates = {
        "mid": _monthly(assume.fund_return_bp),
        "low": _monthly(assume.fund_low_bp),
        "high": _monthly(assume.fund_high_bp),
    }
    cash = float(cash_paise)
    funds = {k: float(funds_paise) for k in rates}
    active_assets = [a for a in assets if a.status == "active"]
    active_loans = [loan for loan in loans if loan.status == "active"]

    def debt_on(day: date) -> int:
        return sum(loan_state(loan, day)["outstanding_paise"] for loan in active_loans)

    def saved_on(day: date) -> int:
        return sum(asset_value(a, day) for a in active_assets)

    start_assets = saved_on(today)
    start_debt = debt_on(today)
    points = []
    for i in range(1, months + 1):
        month_start = add_months(this_month, i)
        inc = income * (1 + g_income) ** i
        out = base_spend * (1 + g_spend) ** i
        emis = sum(emi_in_month(loan, month_start) for loan in active_loans)
        put_in = existing_sip + assume.new_sip_paise
        cash += inc - out - emis - put_in - contributions
        for k, r in rates.items():
            funds[k] = funds[k] * (1 + r) + put_in
        saved = saved_on(month_start)
        debt = debt_on(month_start)
        mid = cash + funds["mid"] + saved - debt
        spread = sigma * math.sqrt(i)
        points.append(
            {
                "month": f"{month_start.year:04d}-{month_start.month:02d}",
                "net_worth_paise": round(mid),
                "low_paise": round(cash - spread + funds["low"] + saved - debt),
                "high_paise": round(cash + spread + funds["high"] + saved - debt),
                "cash_paise": round(cash),
                "funds_paise": round(funds["mid"]),
                "savings_paise": saved,
                "debt_paise": debt,
                "emis_paise": emis,
            }
        )
    surplus_now = income - spend_all - existing_sip - contributions
    return {
        "start": {
            "net_worth_paise": cash_paise + funds_paise + start_assets - start_debt,
            "cash_paise": cash_paise,
            "funds_paise": funds_paise,
            "savings_paise": start_assets,
            "debt_paise": start_debt,
        },
        "inputs": {
            "income_paise": income,
            "spend_paise": spend_all,
            "emis_paise": emis_now,
            "contributions_paise": contributions,
            "existing_investing_paise": existing_sip,
            "surplus_paise": surplus_now,
            "surplus_spread_paise": round(sigma),
            "months_of_history": len(history.income),
        },
        "points": points,
    }


def insights(
    *,
    today: date,
    liquid_paise: int,
    spend_paise: int,
    surplus_paise: int,
    income_paise: int,
    loans: list[tuple[str, Loan]],
    assume: Assumptions,
) -> list[dict]:
    """Plain observations for the Plan page, most useful first. Each has a tone (good, warning
    or info) and words; nothing here is advice to buy any particular product."""
    out: list[dict] = []
    if spend_paise > 0:
        months = liquid_paise / spend_paise
        target = assume.emergency_months
        if months < target:
            gap = target * spend_paise - liquid_paise
            out.append(
                {
                    "tone": "warning",
                    "title": f"Emergency fund covers {months:.1f} months",
                    "body": f"Aim for {target} months of spending in the bank or a sweep FD "
                    f"before investing for growth: about {_inr(gap)} more.",
                }
            )
        else:
            out.append(
                {
                    "tone": "good",
                    "title": f"Emergency fund covers {months:.1f} months",
                    "body": f"That's past the {target} months to keep aside, so extra savings "
                    "can go towards longer-term goals.",
                }
            )
    if income_paise > 0:
        rate = (income_paise - spend_paise) / income_paise
        goal = assume.save_target_bp / 10000
        tone = "good" if rate >= goal else "warning" if rate < goal / 2 else "info"
        out.append(
            {
                "tone": tone,
                "title": f"Saving {rate:.0%} of income",
                "body": "Over the last three months, after spending and EMIs, counting what "
                f"went into RDs, PPF or funds. Your target is {goal:.0%}.",
            }
        )
    for name, loan in loans:
        state = loan_state(loan, today)
        if state["done"]:
            continue
        out.append(
            {
                "tone": "info",
                "title": f"{name} ends {state['ends_on']:%B %Y}",
                "body": f"{state['left']} instalments of {_inr(loan.emi_paise)} left. That "
                "money frees up after, and the forecast counts it.",
            }
        )
    covered = spend_paise > 0 and liquid_paise / spend_paise >= assume.emergency_months
    if surplus_paise > 0 and spend_paise > 0 and not covered:
        gap = assume.emergency_months * spend_paise - liquid_paise
        months = math.ceil(gap / surplus_paise)
        out.append(
            {
                "tone": "info",
                "title": f"Emergency fund full by {add_months(today, months):%B %Y}",
                "body": f"Keeping the whole monthly surplus of {_inr(surplus_paise)} in the bank "
                f"or a sweep FD closes the {_inr(gap)} gap in about {months} month"
                f"{'s' if months != 1 else ''}. Investing for growth comes after.",
            }
        )
    if surplus_paise > 0 and assume.new_sip_paise == 0 and covered:
        half = surplus_paise // 2 // 50000 * 50000 or surplus_paise // 2
        five = future_value_monthly(half, assume.fund_return_bp, 60)
        out.append(
            {
                "tone": "info",
                "title": f"Investing {_inr(half)} a month",
                "body": f"About half the monthly surplus. At {assume.fund_return_bp / 100:g}% a "
                f"year it would be about {_inr(five)} in five years ({_inr(half * 60)} put in). "
                "Try it with the slider to see the forecast move.",
            }
        )
    return out


# --- Financial health ---------------------------------------------------------------------------

# Rules of thumb the checks use. Each is a common Indian personal-finance guideline, kept here so
# the page, the assistant and the tests agree.
EMI_GOOD, EMI_LIMIT = 0.30, 0.40  # EMIs as a share of take-home pay
CARD_GOOD, CARD_LIMIT = 0.30, 0.50  # credit card balance as a share of the limit
HEALTH_COVER_GOOD = 10_00_000_00  # ₹10 lakh of health cover for one adult in a city
LIFE_COVER_YEARS = 10  # term cover of ten years' income when someone depends on you
PRIORITY = ("cards", "bills", "emergency", "health", "emis", "life", "saving")


@dataclass
class Snapshot:
    """What the health checks need, gathered by the store. Monthly figures are the last three
    full months' averages; "month" figures are this calendar month so far."""

    income_paise: int
    spend_paise: int
    essential_paise: int
    emis_paise: int
    liquid_paise: int
    cash_paise: int
    balances_known: bool
    cards: int
    card_owed_paise: int
    card_limit_paise: int
    overdue_bills: int
    due_30_paise: int
    month_income_paise: int
    month_spent_paise: int
    month_committed_paise: int
    days_left: int
    months_of_history: int


def _check(key, title, status, value, target, body, link=None) -> dict:
    return {
        "key": key,
        "title": title,
        "status": status,
        "value": value,
        "target": target,
        "body": body,
        "link": link,
    }


def _ladder(share: float, good: float, limit: float) -> str:
    return "good" if share <= good else "watch" if share <= limit else "act"


def health(snap: Snapshot, assume: Assumptions) -> dict:
    """How safe the money is: a handful of checks with a status each (good, watch, act or
    unknown), what's safe to spend for the rest of this month, the balance between essentials,
    lifestyle and saving, and the one next step that matters most."""
    checks: dict[str, dict] = {}
    spend, income = snap.spend_paise, snap.income_paise
    target = assume.emergency_months

    if spend > 0 and snap.balances_known:
        months = snap.liquid_paise / spend
        status = "good" if months >= target else "watch" if months >= 3 else "act"
        gap = max(target * spend - snap.liquid_paise, 0)
        checks["emergency"] = _check(
            "emergency",
            "Emergency fund",
            status,
            f"{months:.1f} months",
            f"{target} months",
            "Money you can reach in a day (bank and FDs) against your usual monthly spending. "
            + (
                f"About {_inr(gap)} more reaches {target} months."
                if gap
                else "Enough to ride out a job change or a big bill."
            ),
            "/money/plan",
        )
    else:
        checks["emergency"] = _check(
            "emergency",
            "Emergency fund",
            "unknown",
            "Not known",
            f"{target} months",
            "Add bank balances on the Accounts tab and a month of transactions to see how many "
            "months your savings would cover.",
            "/money/accounts",
        )

    goal = assume.save_target_bp / 10000
    if income > 0:
        rate = (income - spend) / income
        status = "good" if rate >= goal else "watch" if rate >= goal / 2 else "act"
        checks["saving"] = _check(
            "saving",
            "Saving rate",
            status,
            f"{rate:.0%} of income",
            f"{goal:.0%} or more",
            "What's left after spending and EMIs over the last three months, including what went "
            "into RDs, PPF or funds.",
            "/money/budgets",
        )
    else:
        checks["saving"] = _check(
            "saving",
            "Saving rate",
            "unknown",
            "Not known",
            f"{goal:.0%} or more",
            "No salary or other income seen yet. Import a statement or switch on alert emails.",
            "/money/settings",
        )

    if income > 0:
        share = snap.emis_paise / income
        checks["emis"] = _check(
            "emis",
            "EMIs against income",
            _ladder(share, EMI_GOOD, EMI_LIMIT),
            f"{share:.0%}" if snap.emis_paise else "No EMIs",
            f"under {EMI_GOOD:.0%}",
            "Loans and card EMIs each month as a share of take-home pay. Above "
            f"{EMI_LIMIT:.0%} leaves little room when something goes wrong.",
            "/money/accounts",
        )

    if snap.cards:
        if snap.overdue_bills:
            status, value = "act", f"{snap.overdue_bills} bill overdue"
        elif snap.card_limit_paise:
            share = snap.card_owed_paise / snap.card_limit_paise
            status, value = _ladder(share, CARD_GOOD, CARD_LIMIT), f"{share:.0%} of limit"
        else:
            status, value = "unknown", "Limit not known"
        checks["cards"] = _check(
            "cards",
            "Credit cards",
            status,
            value,
            f"under {CARD_GOOD:.0%}, paid in full",
            "An unpaid card bill costs 36 to 45% a year in interest and hurts your credit score; "
            "using little of the limit helps it.",
            "/money/accounts",
        )

    if snap.balances_known:
        ok = snap.cash_paise >= snap.due_30_paise
        checks["bills"] = _check(
            "bills",
            "Next 30 days covered",
            "good" if ok else "act",
            f"{_inr(snap.cash_paise)} for {_inr(snap.due_30_paise)} due",
            "bank balance covers it",
            "Card bills, EMIs and renewals due in the next 30 days against what's in the bank now.",
            "/money",
        )

    cover = assume.health_cover_paise
    checks["health"] = _check(
        "health",
        "Health insurance",
        "unknown"
        if cover is None
        else "good"
        if cover >= HEALTH_COVER_GOOD
        else "watch"
        if cover > 0
        else "act",
        "Not told" if cover is None else _inr(cover) if cover else "None",
        f"{_inr(HEALTH_COVER_GOOD)} or more of your own",
        "A hospital stay can undo years of saving. Cover through work ends with the job, so a "
        "policy of your own matters.",
        None,
    )

    life = assume.life_cover_paise
    need = income * 12 * LIFE_COVER_YEARS
    if assume.dependants is None:
        status, value = "unknown", "Not told"
    elif not assume.dependants:
        status, value = "good", "No one depends on you yet"
    else:
        status = "good" if life and life >= need else "watch" if life else "act"
        value = _inr(life) if life else "None"
    checks["life"] = _check(
        "life",
        "Life cover",
        status,
        value,
        f"{LIFE_COVER_YEARS}x yearly income if someone depends on you",
        "Plain term insurance, not an investment plan. Only needed once someone relies on your "
        "income.",
        None,
    )

    # Safe to spend for the rest of this month: expected income, less the saving target, what's
    # already gone and what's still due (EMIs, renewals).
    expected = max(snap.month_income_paise, income)
    keep = round(expected * goal)
    left = expected - keep - snap.month_spent_paise - snap.month_committed_paise
    safe = {
        "known": expected > 0,
        "expected_income_paise": expected,
        "keep_paise": keep,
        "spent_paise": snap.month_spent_paise,
        "committed_paise": snap.month_committed_paise,
        "left_paise": left,
        "per_day_paise": max(left, 0) // max(snap.days_left, 1),
        "days_left": snap.days_left,
        "status": "unknown" if expected <= 0 else "good" if left >= 0 else "act",
    }

    essentials = min(snap.essential_paise, spend)
    balance = {
        "income_paise": income,
        "essentials_paise": essentials,
        "lifestyle_paise": max(spend - essentials, 0),
        "saved_paise": income - spend,
        "guide": {"essentials": 0.5, "lifestyle": 0.3, "saved": 0.2},
    }

    order = [checks[k] for k in PRIORITY if k in checks]
    pick = next(
        (c for want in ("act", "watch", "unknown") for c in order if c["status"] == want), None
    )
    counts = {s: sum(1 for c in order if c["status"] == s) for s in ("good", "watch", "act")}
    counts["unknown"] = len(order) - sum(counts.values())
    verdict = (
        "act" if counts["act"] else "watch" if counts["watch"] or counts["unknown"] else "good"
    )
    ready = (
        all(checks[k]["status"] == "good" for k in ("emergency", "cards", "bills") if k in checks)
        and checks["health"]["status"] != "act"
    )
    return {
        "verdict": verdict,
        "counts": counts,
        "checks": order,
        "next_step": pick,
        "safe_to_spend": safe,
        "balance": balance,
        "ready_to_invest": ready and checks["emergency"]["status"] == "good",
        "months_of_history": snap.months_of_history,
    }


def _inr(p: int) -> str:
    """₹1.2L / ₹45,000 style, for sentences."""
    rupees = p / 100
    if abs(rupees) >= 1e7:
        return f"₹{_trim(rupees / 1e7)}\u00a0Cr"
    if abs(rupees) >= 1e5:
        return f"₹{_trim(rupees / 1e5)}\u00a0L"
    whole = f"{round(rupees):,}"
    return f"₹{whole}"


def _trim(x: float) -> str:
    """2.50 -> 2.5, 10.00 -> 10."""
    return f"{x:.2f}".rstrip("0").rstrip(".")
