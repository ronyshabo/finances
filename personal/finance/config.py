"""Local settings, stored next to the project in config.json (git-ignored).

Financial data lives in the GridCraft master workbook; this file only keeps
connection details and planner defaults.
"""
import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config.json"

# GridCraft runs as a Flatpak that can only see ~/Documents and ~/Downloads,
# so the master workbook has to live in one of those.
DEFAULTS = {
    "gridcraft_port": 7979,
    "master_path": str(Path.home() / "Documents" / "PersonalFinance" / "Finance_Master.xlsx"),
    "flatpak_app_id": "ai.storyteller.gridcraft",
    "launch_timeout_s": 30,
    # Claude advisor tab: the smallest current model, answers capped to keep cost low.
    "advisor_model": "claude-haiku-5-5",
    "advisor_max_tokens": 16000,
    # Your name in the BRB payroll summary (../brb/data), for your barista earnings.
    "brb_owner_name": "Rony",
    "plan": {
        "fixed_weekly_pay": 400.0,
        "raise_to": 500.0,
        "raise_in_weeks": 12,
        "weekly_allowance": 150.0,
        "tax_set_aside_pct": 24.1,
        "income_change_pct": 0.0,
        "weekly_bills": 45.0,
        "strategy": "avalanche",
        "emergency_fund_target": 1000.0,
        "invest_pct_after_debt": 50.0,
        "invest_return_pct": 7.0,
        "savings_apy_pct": 4.0,
        "horizon_weeks": 156,
        "keep_cards_paid_off": True,
    },
    # File name -> statement kind. Named groups: y/m (period), last4 (card).
    # Checked in order; the first match wins.
    "filename_patterns": [
        [r"^(?P<y>\d{4})(?P<m>\d{2})\d{2}-Bank statement", "bank"],
        [r"^Statement_(?P<m>\d{2})(?P<y>\d{4})_(?P<last4>\d{4})", "card"],
        [r"brb|earnings", "brb"],
    ],
    "bank_name": "Capital One 360",
    # Card last four digits -> the name used on the Accounts / Transactions sheets.
    # Real cards go in config.json (git-ignored), e.g. {"cards": {"1234": "Visa 1234"}}.
    "cards": {},
    # Lower-cased keyword -> category, checked in order (first match wins).
    "category_rules": {
        "interest charge": "Interest", "monthly interest paid": "Interest Earned",
        "mobile pmt": "Card Payment", "online pmt": "Card Payment", "crcardpmt": "Card Payment",
        "mobile pymt": "Card Payment", "online pymt": "Card Payment", "returned mobile ach": "Card Payment",
        "payment thank you": "Card Payment", "autopay": "Card Payment", "statement credit": "Rewards",
        "zelle money received": "Income", "cash added": "Cash Deposit", "payroll": "Income",
        "zelle money sent": "Zelle Sent", "venmo": "Venmo", "atm withdrawal": "Cash",
        "h-e-b": "Groceries", "h e b": "Groceries", "heb ": "Groceries", "walmart": "Groceries",
        "restaurant depot": "Groceries", "tehran market": "Groceries", "costco": "Groceries",
        "seven eleven": "Groceries",
        "shell": "Gas", "chevron": "Gas", "exxon": "Gas", "valero": "Gas", "union 76": "Gas", "buc-ee": "Gas",
        "vape": "Vape/Smoke", "smoker": "Vape/Smoke",
        "amazon web services": "Subscriptions", "aws": "Subscriptions", "anthropic": "Subscriptions", "google": "Subscriptions", "youtube": "Subscriptions",
        "amazon prime": "Subscriptions", "hims": "Subscriptions", "netflix": "Subscriptions",
        "spotify": "Subscriptions", "apple.com": "Subscriptions",
        "airlines": "Travel", "hotel": "Travel", "lax ": "Travel", "haneda": "Travel", "hudson st": "Travel",
        "wifi": "Travel", "smartecarte": "Travel", "santa monica": "Travel", "los angeles": "Travel",
        "gofndme": "Gifts/Donations", "souvenir": "Gifts/Donations",
        "target": "Shopping", "amazon": "Shopping", "amzn": "Shopping", "home depot": "Shopping",
        "surplus stor": "Shopping",
        "whataburger": "Eating Out", "taco": "Eating Out", "taqueria": "Eating Out", "cheba hut": "Eating Out",
        "shawarma": "Eating Out", "al shami": "Eating Out", "chili s": "Eating Out", "dairy queen": "Eating Out",
        "p terry": "Eating Out", "frazier": "Eating Out", "brasserie": "Eating Out", "pizzeria": "Eating Out",
        "jamba": "Eating Out", "espresso": "Eating Out", "pret a manger": "Eating Out", "odd one out": "Eating Out",
        "bar ": "Bars", "pub": "Bars", "back lot": "Bars", "lounge": "Bars", "loun": "Bars",
        "parkpca": "Entertainment", "bigdash": "Entertainment", "steam": "Entertainment",
        "uber": "Transport", "lyft": "Transport",
        "rent": "Rent", "electric": "Utilities", "at&t": "Phone", "t-mobile": "Phone", "verizon": "Phone",
        "geico": "Insurance", "progressive": "Insurance", "ameritas": "Insurance", "fee": "Fees",
        " jp": "Travel", "osak": "Travel", "portland": "Travel", "pdx": "Travel",
        "name-cheap": "Subscriptions", "namecheap": "Subscriptions",
        "cava": "Eating Out", "via 313": "Eating Out", "sushi": "Eating Out", "bakery": "Eating Out",
        "champys": "Eating Out", "liquor": "Bars", "fiesta mart": "Groceries", "market": "Groceries",
        "ups*": "Shopping", "barton springs": "Entertainment",
    },
}


def _merge(base, override):
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load():
    if CONFIG_PATH.exists():
        try:
            return _merge(DEFAULTS, json.loads(CONFIG_PATH.read_text()))
        except (OSError, ValueError):
            pass
    return _merge(DEFAULTS, {})


def save(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
