"""Link to the BRB owner payout planner in ../brb (same repo).

Reads the shop's data folder with the BRB app's own brb_data module, read-only,
so both apps agree on the numbers. Nothing in brb/ is written or changed.
"""
import csv
import subprocess
import sys

from .config import PROJECT_DIR

BRB_DIR = PROJECT_DIR.parent / "brb"
BRB_APP = BRB_DIR / "app" / "brb_app.py"
GROWTH_WK = 200.0  # the BRB planner's default growth money per week


def _brb_data():
    if not BRB_APP.exists():
        raise FileNotFoundError(f"BRB planner not found at {BRB_DIR}")
    sys.path.insert(0, str(BRB_APP.parent))
    try:
        import brb_data
    finally:
        sys.path.remove(str(BRB_APP.parent))
    return brb_data


def _my_payroll(data, name):
    """This year's barista earnings for one person from the newest payroll summary."""
    files = [f for f in data.files if f.kind == "payroll" and f.status == "OK"]
    if not files:
        return None
    with open(files[-1].path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.reader(fh):
            if len(r) > 11 and r[0].strip().lower() == name.lower() and r[1].strip().isdigit():
                weeks = float(r[2])
                return {"year": int(r[1]), "weeks": weeks, "hours": float(r[4]), "base": float(r[5]),
                        "tips": float(r[6]), "gross": float(r[7]), "last_week": r[11],
                        "avg_week": float(r[7]) / weeks if weeks else 0.0}
    return None


def snapshot(owner_name):
    """Owner payout at today's prices plus this owner's barista earnings.

    Returns a dict, or raises if the BRB planner or its data can't be read.
    """
    bd = _brb_data()
    data = bd.load(bd.DEFAULT_FOLDER)
    r = bd.compute(data, GROWTH_WK, price=0.0, loss=0.0)
    return {
        "per_owner": r["per_owner"], "owners": r["owners"], "pool": r["pool"],
        "base_sales": r["base_sales"], "weeks": r["weeks"], "bills_m": r["bills_m"],
        "cogs_pct": r["cogs_pct"], "growth": GROWTH_WK,
        "cash": data.cash, "warnings": list(data.warnings),
        "me": _my_payroll(data, owner_name),
    }


def describe(s):
    """Plain-text lines for the advisor summary and the Plan tab."""
    lines = [f"- Owner payout estimate: ${s['per_owner']:,.2f}/week each ({s['owners']} owners), at today's "
             f"prices after cost of goods ({s['cogs_pct']:.0%}), fees, bills, staff pay and "
             f"${s['growth']:,.0f}/week growth money",
             f"- Shop sales: ${s['base_sales']:,.2f}/week average over {s['weeks']} full weeks; "
             f"monthly bills on file ${s['bills_m']:,.2f}"]
    if s["cash"]:
        lines.append(f"- Shop cash in bank: ${s['cash'][1]:,.2f} as of {s['cash'][0]:%b %d, %Y}")
    me = s["me"]
    if me:
        lines.append(f"- My barista earnings {me['year']}: ${me['gross']:,.2f} over {me['weeks']:.0f} weeks "
                     f"(${me['avg_week']:,.2f}/week; tips ${me['tips']:,.2f}), last week paid {me['last_week']}")
    lines += [f"- BRB data note: {w}" for w in s["warnings"]]
    return lines


def open_planner():
    subprocess.Popen([sys.executable, str(BRB_APP)], cwd=str(BRB_DIR),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
