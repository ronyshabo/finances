"""Monthly money coach backed by the Claude API.

Claude gets a compact summary (balances, monthly category totals, recurring
charges, top merchants, income and the current plan), not raw statements.
"""
import os
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

KEY_FILE = Path.home() / ".config" / "personal-finance" / "anthropic_key"

SYSTEM_PROMPT = """You are a practical personal-finance coach for one person. They co-own a coffee \
shop (BRB) and work barista shifts there, paid as a 1099 contractor in Texas (no state income tax, \
nothing withheld). They are paying off two credit cards, and want to build an emergency fund and \
then start investing. Their pay is moving to a stable fixed weekly amount, and later to an owner \
payout once they step out of shifts.

You get a data summary from their finance app. Base every recommendation on those numbers, and \
show the arithmetic when you give an amount. Be direct and specific: name the merchants, \
categories and dollar figures that matter. Point out risks (a payment that won't be covered, \
taxes owed, overspending trends) plainly but kindly. Keep it short enough to act on.

Write plain text for a small desktop window: short headings in CAPS, "-" bullets, no markdown \
tables, no bold markers. You are not a licensed financial advisor; say so once only if you \
recommend a specific investment product."""

PROMPTS = {
    "Prep me for next month": (
        "Prepare me for next month. Give me: (1) a week-by-week budget for that month using my "
        "weekly pay, allowance and bills, (2) what to pay on each credit card and when, (3) the "
        "recurring charges I should expect and which ones to cancel or cut, (4) how much to move "
        "into the tax set-aside and any tax dates coming up, and (5) the 3 most important "
        "habits to change, based on last month's spending."),
    "Review last month": (
        "Review my most recent full month. Where did the money go compared with my plan, what "
        "went well, what went badly, and what's the one change that would help most?"),
    "Reach $500/wk faster": (
        "My pay is $400/week and may go to $500. What would change in my plan at $500, and is "
        "there anything I can do now so the raise goes straight to savings and investing "
        "instead of getting spent?"),
    "Taxes I owe": (
        "Using my BRB earnings and the tax set-aside, how much do I likely owe for this year, "
        "what estimated-payment dates are coming up, and how do I fit catching up into my plan "
        "without missing card payments?"),
}


def load_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    try:
        return KEY_FILE.read_text().strip() or None
    except OSError:
        return None


def save_key(key):
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(key.strip())
    KEY_FILE.chmod(0o600)


def _merchant(desc):
    """'Digital Card Purchase - WHATABURGER 1191 AUSTIN TX' -> 'WHATABURGER'"""
    d = re.sub(r"^(Debit|Digital) Card Purchase - |^Withdrawal from |^TST\*?\s*|^SQ \*?", "", desc.strip(),
               flags=re.IGNORECASE)
    words = []
    for w in d.replace("*", " ").split():
        if any(ch.isdigit() for ch in w) or w in ("-", "#"):  # store numbers, codes, phone numbers
            break
        words.append(w)
    return " ".join(words[:3]).upper() or desc[:20].upper()


def build_summary(income, transactions, accounts, plan_inputs, plan_result, today=None, business=None):
    today = today or date.today()
    out = [f"TODAY: {today:%A %B %d, %Y}"]

    p, r = plan_inputs, plan_result
    out.append("\nPLAN SETTINGS")
    out.append(f"- Fixed weekly pay (before tax), paid every week regardless of tips: ${p.weekly_gross:,.2f}"
               + (f", rising to ${p.raise_to:,.2f} in {p.raise_in_weeks} weeks" if p.raise_to else ""))
    out.append(f"- Tax set-aside: {p.tax_set_aside_pct:.1f}% -> ${r['weekly_gross'] - r['weekly_net']:,.2f}/week")
    out.append(f"- Weekly allowance: ${p.weekly_allowance:,.2f}; fixed bills: ${p.weekly_bills:,.2f}/week")
    out.append(f"- Left for cards/savings each week: ${r['weekly_spare']:,.2f}")
    out.append(f"- Payoff strategy: {p.strategy}; order: {' -> '.join(r['payoff_order']) or 'no card debt'}")
    out.append(f"- Emergency fund goal ${p.emergency_fund_target:,.0f}; after debt, invest "
               f"{p.invest_pct_after_debt:.0f}% of spare at an assumed {p.invest_return_pct:.1f}%/yr")
    if r["debt_free_week"]:
        out.append(f"- Simulated: cards paid off in week {r['debt_free_week']} "
                   f"({today + timedelta(weeks=r['debt_free_week']):%b %d, %Y}), interest ${r['total_interest']:,.2f}")
    if r["fund_full_week"]:
        out.append(f"- Simulated: emergency fund full in week {r['fund_full_week']}")

    if business:  # lines from finance.business.describe
        out.append("\nBRB COFFEE SHOP (I'm one of the owners; from the shop's owner payout planner)")
        out.extend(business)

    out.append("\nACCOUNTS (latest statements)")
    for a in accounts:
        bits = [f"- {a.get('Name')} ({a.get('Type')}): balance ${float(a.get('Balance') or 0):,.2f}"]
        if a.get("Type") == "credit":
            bits.append(f"APR {a.get('APR %')}%, minimum ${float(a.get('Min Payment') or 0):,.2f}/month")
        if a.get("As Of"):
            bits.append(f"as of {a['As Of']}")
        if a.get("Notes"):
            bits.append(str(a["Notes"]))
        out.append(", ".join(bits))

    out.append("\nINCOME STATEMENTS (BRB, 1099)")
    for i in sorted(income, key=lambda i: i.get("Period End") or date.min):
        out.append(f"- {i.get('Period Start')} to {i.get('Period End')}: gross ${float(i.get('Gross') or 0):,.2f} "
                   f"(base ${float(i.get('Base Pay') or 0):,.2f}, tips ${float(i.get('Tips') or 0):,.2f}), "
                   f"{i.get('Shifts')} shifts / {i.get('Hours')} hours, "
                   f"estimated tax to set aside ${float(i.get('Tax Set Aside') or 0):,.2f}")

    dated = [t for t in transactions if t.get("Date")]
    by_month = defaultdict(lambda: defaultdict(float))
    money_in = defaultdict(float)
    for t in dated:
        amt = float(t.get("Amount") or 0)
        month = f"{t['Date']:%Y-%m}"
        cat = t.get("Category") or "Other"
        if cat in ("Card Payment", "Interest Earned", "Rewards"):
            continue
        if amt < 0:
            by_month[month][cat] += -amt
        elif cat in ("Income", "Cash Deposit"):
            money_in[month] += amt
    out.append("\nSPENDING BY MONTH (checking + both cards; card payments excluded)")
    for month in sorted(by_month)[-3:]:
        cats = sorted(by_month[month].items(), key=lambda kv: -kv[1])  # category totals, all accounts
        out.append(f"- {month}: total ${sum(v for _, v in cats):,.2f} | "
                   + ", ".join(f"{c} ${v:,.0f}" for c, v in cats))
    out.append("Money received by month (Zelle/deposits): "
               + ", ".join(f"{m} ${v:,.0f}" for m, v in sorted(money_in.items())[-3:]))

    months_seen = defaultdict(set)
    amounts = defaultdict(list)
    for t in dated:
        amt = float(t.get("Amount") or 0)
        if amt < 0 and t.get("Category") not in ("Card Payment", "Interest"):
            m = _merchant(t.get("Description", ""))
            months_seen[m].add(f"{t['Date']:%Y-%m}")
            amounts[m].append(-amt)
    recurring = [(m, sum(amounts[m]) / len(amounts[m])) for m in months_seen if len(months_seen[m]) >= 2]
    subs = {_merchant(t.get("Description", "")) for t in dated if t.get("Category") == "Subscriptions"}
    out.append("\nRECURRING OR SUBSCRIPTION CHARGES (seen in 2+ months, or tagged Subscriptions)")
    for m in sorted(set(dict(recurring)) | subs):
        avg = sum(amounts[m]) / len(amounts[m])
        out.append(f"- {m}: avg ${avg:,.2f} per charge, {len(amounts[m])} charges")

    if dated:
        cutoff = max(t["Date"] for t in dated) - timedelta(days=60)
        kinds = {a.get("Name"): a.get("Type") for a in accounts}
        top = defaultdict(float)
        paid_from = defaultdict(set)
        for t in dated:
            if t["Date"] >= cutoff and float(t.get("Amount") or 0) < 0 \
                    and t.get("Category") not in ("Card Payment", "Interest"):
                m = _merchant(t.get("Description", ""))
                top[m] += -float(t["Amount"])
                paid_from[m].add(f"{t.get('Account')} ({kinds.get(t.get('Account'), '?')})")
        out.append("\nTOP MERCHANTS, LAST 60 DAYS OF DATA (and which account paid)")
        for m, v in sorted(top.items(), key=lambda kv: -kv[1])[:15]:
            out.append(f"- {m}: ${v:,.2f}, paid from {', '.join(sorted(paid_from[m]))}")
        out.append(f"\nData covers {min(t['Date'] for t in dated)} to {max(t['Date'] for t in dated)}.")
    return "\n".join(out)


# Claude Haiku 5.5 list prices, USD per million tokens (prompts up to 100K tokens)
PRICE_IN, PRICE_OUT = 0.10, 0.50


class Advisor:
    """A conversation with Claude about the user's finances."""

    def __init__(self, model, max_tokens):
        self.model = model
        self.max_tokens = max_tokens
        self.messages = []
        self.spent = 0.0  # estimated USD for this conversation

    def reset(self):
        self.messages = []
        self.spent = 0.0

    def ask(self, question, summary, on_text):
        """Stream an answer, calling on_text(chunk) as text arrives.

        Returns (text, cost_usd) for this answer.
        """
        import anthropic

        if not self.messages:  # the data summary opens the conversation
            content = f"Here is my current financial data:\n\n{summary}\n\n{question}"
        else:
            content = question
        self.messages.append({"role": "user", "content": content})
        client = anthropic.Anthropic(api_key=load_key())
        with client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            messages=self.messages,
            # raw body field so older SDK versions don't reject it; low effort keeps answers cheap
            extra_body={"output_config": {"effort": "low"}},
        ) as stream:
            for text in stream.text_stream:
                on_text(text)
            final = stream.get_final_message()
        if final.stop_reason == "refusal":
            self.messages.pop()
            raise RuntimeError("Claude declined to answer that request.")
        self.messages.append({"role": "assistant", "content": final.content})
        if final.stop_reason == "max_tokens":
            on_text("\n\n[answer reached the length limit]")
        u = final.usage
        cost = (u.input_tokens * PRICE_IN + u.output_tokens * PRICE_OUT) / 1_000_000
        self.spent += cost
        return "".join(b.text for b in final.content if b.type == "text"), cost
