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


def _recent(values: list[int], n: int = 3) -> int:
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
    income = assume.income_paise if assume.income_paise is not None else _recent(history.income)
    spend_all = assume.spend_paise if assume.spend_paise is not None else _recent(history.spend)
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
    existing_sip = max(_recent(history.invested) - monthly_debits, 0)
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
        rate = surplus_paise / income_paise
        tone = "good" if rate >= 0.2 else "warning" if rate < 0.1 else "info"
        out.append(
            {
                "tone": tone,
                "title": f"Saving {rate:.0%} of income",
                "body": "Over the last three months, after spending, EMIs and what already "
                "goes into savings. 20% or more is a comfortable rate.",
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
    if surplus_paise > 0 and assume.new_sip_paise == 0:
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


def _inr(p: int) -> str:
    """₹1.2L / ₹45,000 style, for sentences."""
    rupees = p / 100
    if abs(rupees) >= 1e7:
        return f"₹{rupees / 1e7:.2f} Cr"
    if abs(rupees) >= 1e5:
        return f"₹{rupees / 1e5:.2f} L"
    whole = f"{round(rupees):,}"
    return f"₹{whole}"
