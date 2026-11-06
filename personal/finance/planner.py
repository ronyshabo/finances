"""Week-by-week money plan: allowance, card payoff, emergency fund, investing.

Each simulated week:
  1. net income = gross * (1 - tax set-aside)
  2. allowance and bills come out
  3. card interest accrues (APR / 52), minimums are paid
  4. a small starter buffer is built, then every spare dollar goes to one
     card at a time (avalanche = highest APR first, snowball = smallest balance)
  5. once cards are clear: emergency fund to its target, then the split
     between investing and savings
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

WEEKS_PER_MONTH = 52 / 12


@dataclass
class Card:
    name: str
    balance: float
    apr: float          # percent
    min_payment: float  # per month


@dataclass
class PlanInputs:
    weekly_gross: float
    raise_to: float = 0.0       # weekly gross after a raise (0 = no raise)
    raise_in_weeks: int = 0     # weeks from now until the raise starts
    tax_set_aside_pct: float = 24.1
    income_change_pct: float = 0.0
    weekly_allowance: float = 75.0
    weekly_bills: float = 0.0
    cards: list = field(default_factory=list)
    starting_cash: float = 0.0
    starter_buffer: float = 300.0
    strategy: str = "avalanche"
    emergency_fund_target: float = 1000.0
    invest_pct_after_debt: float = 50.0
    invest_return_pct: float = 7.0
    savings_apy_pct: float = 4.0
    horizon_weeks: int = 156
    start: date = field(default_factory=date.today)


def weekly_income_from_records(income_recs):
    """Average weekly gross across BRB statements.

    Statements can be year-to-date summaries that overlap, so periods are
    picked latest-first and any that overlap an already-picked one are skipped.
    Returns (weekly_gross, tax_rate_pct, weeks_covered).
    """
    recs = sorted((r for r in income_recs if r.get("Period Start") and r.get("Period End") and r.get("Gross")),
                  key=lambda r: (r["Period End"], -(r["Period End"] - r["Period Start"]).days), reverse=True)
    picked = []
    for r in recs:
        if all(r["Period End"] < p["Period Start"] or r["Period Start"] > p["Period End"] for p in picked):
            picked.append(r)
    if not picked:
        return 0.0, None, 0.0
    days = sum((r["Period End"] - r["Period Start"]).days + 1 for r in picked)
    gross = sum(float(r["Gross"]) for r in picked)
    aside = sum(float(r.get("Tax Set Aside") or 0) for r in picked)
    weeks = days / 7
    return gross / weeks, (aside / gross * 100 if gross else None), weeks


def _order(cards, strategy):
    live = [c for c in cards if c.balance > 0.005]
    if strategy == "snowball":
        return sorted(live, key=lambda c: c.balance)
    return sorted(live, key=lambda c: (-c.apr, c.balance))


def simulate(p: PlanInputs):
    cards = [Card(c.name, max(c.balance, 0), c.apr, c.min_payment) for c in p.cards]
    cash, invested = p.starting_cash, 0.0
    total_interest = 0.0
    debt_free_week = 0 if not any(c.balance > 0 for c in cards) else None
    fund_full_week = None
    inv_rate = (1 + p.invest_return_pct / 100) ** (1 / 52) - 1
    sav_rate = (1 + p.savings_apy_pct / 100) ** (1 / 52) - 1
    def pay_for(week):
        base = p.raise_to if p.raise_to and week > p.raise_in_weeks else p.weekly_gross
        return base * (1 + p.income_change_pct / 100)

    rows = []
    for w in range(1, p.horizon_weeks + 1):
        gross = pay_for(w)
        net = gross * (1 - p.tax_set_aside_pct / 100)
        spare = net - p.weekly_allowance - p.weekly_bills
        interest = 0.0
        for c in cards:
            i = c.balance * c.apr / 100 / 52
            c.balance += i
            interest += i
        total_interest += interest

        to_cards = 0.0
        for c in cards:  # minimums
            pay = min(c.balance, c.min_payment / WEEKS_PER_MONTH)
            c.balance -= pay
            spare -= pay
            to_cards += pay
        if spare < 0:  # short this week: dip into cash, else the shortfall stays on the cards
            draw = min(cash, -spare)
            cash -= draw
            spare += draw

        to_fund = to_invest = 0.0
        if spare > 0 and any(c.balance > 0.005 for c in cards) and cash < p.starter_buffer:
            to_fund = min(spare, p.starter_buffer - cash)
            spare -= to_fund
        for c in _order(cards, p.strategy):  # extra to one card at a time
            if spare <= 0:
                break
            pay = min(spare, c.balance)
            c.balance -= pay
            spare -= pay
            to_cards += pay
        if spare > 0:
            room = max(p.emergency_fund_target - (cash + to_fund), 0)
            extra_fund = min(spare, room)
            to_fund += extra_fund
            spare -= extra_fund
            to_invest = spare * p.invest_pct_after_debt / 100
            to_fund += spare - to_invest

        cash = cash * (1 + sav_rate) + to_fund
        invested = invested * (1 + inv_rate) + to_invest
        debt = sum(c.balance for c in cards)
        if debt_free_week is None and debt <= 0.005:
            debt_free_week = w
        if fund_full_week is None and cash >= p.emergency_fund_target and debt <= 0.005:
            fund_full_week = w
        rows.append({
            "Week": w, "Week Of": p.start + timedelta(weeks=w - 1), "Income (gross)": round(gross, 2),
            "Tax Set Aside": round(gross - net, 2), "Income (net)": round(net, 2),
            "Allowance": p.weekly_allowance, "Bills": p.weekly_bills, "To Cards": round(to_cards, 2),
            "Card Interest": round(interest, 2), "Card Balance": round(debt, 2),
            "Emergency Fund": round(cash, 2), "Invested": round(invested, 2),
            "Net Worth": round(cash + invested - debt, 2),
            "per_card": {c.name: round(c.balance, 2) for c in cards},
        })
    gross, net = pay_for(1), pay_for(1) * (1 - p.tax_set_aside_pct / 100)
    return {
        "rows": rows,
        "weekly_gross": gross,
        "weekly_net": net,
        "weekly_spare": net - p.weekly_allowance - p.weekly_bills,
        "tax_saved": sum(r["Tax Set Aside"] for r in rows[:52]),
        "min_payments_weekly": sum(c.min_payment for c in p.cards) / WEEKS_PER_MONTH,
        "debt_free_week": debt_free_week,
        "fund_full_week": fund_full_week,
        "total_interest": total_interest,
        "start_debt": sum(c.balance for c in p.cards),
        "payoff_order": [c.name for c in _order([Card(c.name, c.balance, c.apr, c.min_payment)
                                                  for c in p.cards], p.strategy)],
    }


def this_week_breakdown(p: PlanInputs, result):
    """Plain-language plan for the first week."""
    first = result["rows"][0] if result["rows"] else None
    lines = [f"Pay this week: ${result['weekly_gross']:,.2f}",
             f"Set aside for taxes ({p.tax_set_aside_pct:.1f}%): ${result['weekly_gross'] - result['weekly_net']:,.2f}",
             f"Allowance (spend freely): ${p.weekly_allowance:,.2f}"]
    if p.weekly_bills:
        lines.append(f"Bills: ${p.weekly_bills:,.2f}")
    if first:
        lines.append(f"Pay toward cards: ${first['To Cards']:,.2f}")
        if result["payoff_order"]:
            lines.append("   Extra goes to: " + " → ".join(result["payoff_order"]))
        saved = first["Emergency Fund"] - p.starting_cash
        if saved >= 1:
            lines.append(f"Into savings: ${saved:,.2f}")
        if first["Invested"] > 0:
            lines.append(f"Invest: ${first['Invested']:,.2f}")
    if result["weekly_spare"] < result["min_payments_weekly"]:
        lines.append("⚠ Income after allowance and bills doesn't cover card minimums.")
    return lines
