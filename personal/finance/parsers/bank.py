"""Bank / credit card statement parsers.

Recognised layouts (via `pdftotext -layout`):
  * Capital One 360 bank statements (several sub-accounts per statement)
  * Capital One credit cards (e.g. Quicksilver)
  * Discover credit cards
  * anything else: generic CSV export or "date ... amount" text lines

Sign convention for the master sheet: Amount < 0 is money going out of that
account (spending, fees, interest), Amount > 0 is money coming in (deposits;
for a card, payments and credits).

`parse_file` returns a list of account results, because one bank statement
can cover several accounts:
    {"account", "kind", "transactions": [...], "summary": {...}}
"""
import csv
import io
import re
from datetime import date, datetime

from . import read_text

_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%b %d, %Y", "%b %d %Y", "%d %b %Y")
_AMOUNT = r"[-+]?\s?\(?\$\s?[\d,]+\.\d{2}\)?"
_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"


def _parse_date(s, year=None):
    s = re.sub(r"\s+", " ", s.strip())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    if year:
        for fmt in ("%b %d %Y", "%m/%d %Y"):
            try:
                return datetime.strptime(f"{s} {year}", fmt).date()
            except ValueError:
                pass
    return None


def _in_period(s, period_end):
    """Year-less date ('Aug 3', '08/03') placed in the statement's period."""
    d = _parse_date(s, period_end.year)
    if d and d > period_end:  # December rows on a January statement
        d = d.replace(year=d.year - 1)
    return d


def _parse_amount(s):
    s = s.strip().replace("$", "").replace(",", "").replace(" ", "")
    if not s:
        return None
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")")) or s.endswith("-")
    s = s.strip("()-+")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def categorize(desc, rules):
    d = desc.lower()
    for kw, cat in rules.items():
        if kw in d:
            return cat
    return "Other"


def _amount_under(lines, header_i, label):
    """Amount printed in the first non-empty line below `label` in a header row."""
    col = lines[header_i].find(label)
    for line in lines[header_i + 1:header_i + 4]:
        if not line.strip():
            continue
        for m in re.finditer(_AMOUNT, line):
            if abs(m.start() - col) <= 12:
                return _parse_amount(m.group())
        return None
    return None


def _card_summary(text):
    lines = text.splitlines()
    out = {"ending_balance": None, "min_payment": None, "apr": None, "credit_limit": None}
    for i, line in enumerate(lines):
        if "New Balance" in line and "Minimum Payment" in line:
            out["ending_balance"] = _amount_under(lines, i, "New Balance")
            out["min_payment"] = _amount_under(lines, i, "Minimum Payment")
            break
    m = re.search(r"New Balance:?\s*=?\s*(" + _AMOUNT + ")", text)
    if out["ending_balance"] is None and m:
        out["ending_balance"] = _parse_amount(m.group(1))
    m = re.search(r"^\s*Purchases\s+(\d{1,2}\.\d{2})%", text, re.MULTILINE)
    out["apr"] = float(m.group(1)) if m else None
    m = re.search(r"(?:Credit Limit|Credit Line)\s+(" + _AMOUNT + ")", text)
    out["credit_limit"] = _parse_amount(m.group(1)) if m else None
    m = re.search(r"Total Interest for This Period\s+(" + _AMOUNT + ")", text, re.IGNORECASE)
    out["interest"] = _parse_amount(m.group(1)) if m else 0.0
    return out


# ---- Capital One 360 bank ------------------------------------------------------
_C1_ACCOUNT = re.compile(r"^(\S.*?) - (\d{8,})\s*$")
_C1_ROW = re.compile(r"^(" + _MON + r" \d{1,2})\s{2,}(.+?)\s{2,}(?:(Debit|Credit)\s+)?"
                     r"(?:([+-] \$[\d,]+\.\d{2})\s+)?(\$[\d,]+\.\d{2})\s*$")
_C1_WRAP = re.compile(r"^\s+(.*?)\s*([+-] \$[\d,]+\.\d{2})\s*$")


def parse_capital_one_bank(text):
    m = re.search(r"(" + _MON + r" \d{1,2}) - (" + _MON + r" \d{1,2}), (\d{4})", text)
    end = _parse_date(f"{m.group(2)} {m.group(3)}")
    start = _in_period(m.group(1), end)
    lines = text.splitlines()
    results, cur, prev_bal = [], None, None
    for i, line in enumerate(lines):
        am = _C1_ACCOUNT.match(line)
        if am:
            kind_line = next((l.strip() for l in lines[i + 1:i + 3] if l.strip()), "")
            cur = {"account": f"Capital One 360 {am.group(1)}", "number": am.group(2),
                   "kind": "savings" if "SAVINGS" in kind_line.upper() else "checking",
                   "transactions": [],
                   "summary": {"period_start": start, "period_end": end, "ending_balance": None}}
            apy = re.search(r"^\s*(\d+\.\d+)%", "\n".join(lines[i + 1:i + 6]), re.MULTILINE)
            cur["summary"]["apy"] = float(apy.group(1)) if apy else None
            results.append(cur)
            prev_bal = None
            continue
        rm = _C1_ROW.match(line)
        if not rm or cur is None:
            continue
        d, desc, cat, amt_s, bal_s = rm.groups()
        bal = _parse_amount(bal_s)
        if desc.startswith("Opening Balance"):
            prev_bal = bal
            continue
        if desc.startswith("Closing Balance"):
            cur["summary"]["ending_balance"] = bal
            continue
        if amt_s is None:  # long description: the amount wrapped onto the next line
            nxt = next((l for l in lines[i + 1:i + 3] if l.strip()), "")
            wm = _C1_WRAP.match(nxt)
            if wm:
                desc = f"{desc} {wm.group(1)}".strip()
                amt_s = wm.group(2)
        amt = _parse_amount(amt_s) if amt_s else None
        if prev_bal is not None and (amt is None or abs(prev_bal + amt - bal) > 0.005):
            amt = round(bal - prev_bal, 2)  # the running balance is the source of truth
        prev_bal = bal
        if amt is None or abs(amt) < 0.005:
            continue
        desc = re.sub(r"\s{2,}", " ", desc)
        desc = re.sub(r"\s+(Debit|Credit)$", "", desc)
        cur["transactions"].append({"Date": _in_period(d, end), "Description": desc, "Amount": amt})
    return results


# ---- Capital One credit card --------------------------------------------------
_C1_CARD_ROW = re.compile(r"^\s*(" + _MON + r" \d{1,2})\s+" + _MON + r" \d{1,2}\s+(.+?)\s{2,}(" + _AMOUNT + r")\s*$")


def parse_capital_one_card(text):
    m = re.search(r"(" + _MON + r" \d{2}, \d{4}) - (" + _MON + r" \d{2}, \d{4})", text)
    start, end = _parse_date(m.group(1)), _parse_date(m.group(2))
    last4 = re.search(r"ending in (\d{4})", text).group(1)
    name = re.search(r"^\s*(\S.*?) Credit Card\b", text, re.MULTILINE)
    account = f"{name.group(1).strip() if name else 'Capital One'} …{last4}"
    txns = []
    for line in text.splitlines():
        rm = _C1_CARD_ROW.match(line)
        if rm:
            txns.append({"Date": _in_period(rm.group(1), end), "Description": re.sub(r"\s{2,}", " ", rm.group(2)),
                         "Amount": -_parse_amount(rm.group(3))})
    summ = _card_summary(text)
    summ.update(period_start=start, period_end=end)
    if summ["interest"]:
        txns.append({"Date": end, "Description": "Interest charge", "Amount": -summ["interest"]})
    return [{"account": account, "last4": last4, "kind": "credit", "transactions": txns, "summary": summ}]


# ---- Discover -----------------------------------------------------------------
_DISC_ROW = re.compile(r"^\s*(\d{2}/\d{2})\s+(.+?)\s{2,}(" + _AMOUNT + r")(?:\s{2,}.*)?$")


def parse_discover(text):
    m = re.search(r"(\d{2}/\d{2}/\d{4}) - (\d{2}/\d{2}/\d{4})", text)
    start, end = _parse_date(m.group(1)), _parse_date(m.group(2))
    last4 = re.search(r"ending in (\d{4})", text).group(1)
    txns = []
    for line in text.splitlines():
        rm = _DISC_ROW.match(line)
        if rm:
            txns.append({"Date": _in_period(rm.group(1), end), "Description": re.sub(r"\s{2,}", " ", rm.group(2)),
                         "Amount": -_parse_amount(rm.group(3))})
    summ = _card_summary(text)
    summ.update(period_start=start, period_end=end)
    if summ["interest"]:
        txns.append({"Date": end, "Description": "Interest charge", "Amount": -summ["interest"]})
    return [{"account": f"Discover …{last4}", "last4": last4, "kind": "credit", "transactions": txns,
             "summary": summ}]


# ---- generic fallbacks --------------------------------------------------------
def _find_col(headers, *names):
    for i, h in enumerate(headers):
        if any(n in h.strip().lower() for n in names):
            return i
    return None


def parse_csv(text):
    rows = list(csv.reader(io.StringIO(text)))
    hdr_i = next(i for i, r in enumerate(rows) if any("date" in c.lower() for c in r) and len(r) >= 3)
    headers = rows[hdr_i]
    c_date = _find_col(headers, "posting date", "post date", "transaction date", "date")
    c_desc = _find_col(headers, "description", "payee", "merchant", "name", "memo")
    c_amt = _find_col(headers, "amount")
    c_debit = _find_col(headers, "debit", "withdrawal")
    c_credit = _find_col(headers, "credit", "deposit")
    txns = []
    for r in rows[hdr_i + 1:]:
        if not r or c_date is None or c_date >= len(r):
            continue
        d = _parse_date(r[c_date])
        if not d:
            continue
        if c_amt is not None and r[c_amt].strip():
            amt = _parse_amount(r[c_amt])
        else:
            debit = _parse_amount(r[c_debit]) if c_debit is not None else None
            credit = _parse_amount(r[c_credit]) if c_credit is not None else None
            amt = (credit or 0) - abs(debit or 0)
        if amt is not None:
            txns.append({"Date": d, "Description": r[c_desc].strip() if c_desc is not None else "",
                         "Amount": amt})
    return txns


_GENERIC_ROW = re.compile(r"^\s*(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+(.+?)\s{2,}(" + _AMOUNT + r")")


def parse_generic_text(text):
    end = date.today()
    return [{"Date": _in_period(m.group(1), end), "Description": re.sub(r"\s{2,}", " ", m.group(2)),
             "Amount": _parse_amount(m.group(3))}
            for m in map(_GENERIC_ROW.match, text.splitlines()) if m]


# ---- entry point ----------------------------------------------------------------
def detect_layout(text):
    if "Capital One 360" in text or re.search(r"^\S.* - \d{8,}\s*\n\s*360 ", text, re.MULTILINE):
        return "capital_one_bank"
    if "Discover" in text and "OPEN TO CLOSE DATE" in text:
        return "discover"
    if "capitalone.com" in text and re.search(r"Trans Date\s+Post Date", text):
        return "capital_one_card"
    return "generic"


def parse_file(path, account=None, rules=None, card_names=None):
    """Parse a statement. `card_names` maps a card's last four digits to the
    account name to use (otherwise the issuer's name for it)."""
    text = read_text(path)
    layout = "csv" if path.suffix.lower() in (".csv", ".tsv") else detect_layout(text)
    if layout == "capital_one_bank":
        results = parse_capital_one_bank(text)
    elif layout == "capital_one_card":
        results = parse_capital_one_card(text)
    elif layout == "discover":
        results = parse_discover(text)
    else:
        txns = parse_csv(text) if layout == "csv" else parse_generic_text(text)
        results = [{"account": account or path.stem, "kind": "checking", "transactions": txns, "summary": {}}]
    rules = rules or {}
    for res in results:
        res["layout"] = layout
        if res.get("last4") and (card_names or {}).get(res["last4"]):
            res["account"] = card_names[res["last4"]]
        counts = {}
        for t in res["transactions"]:
            base = f"{res['account']}|{t['Date']}|{t['Amount']:.2f}|{t['Description'][:40]}"
            counts[base] = counts.get(base, 0) + 1
            t.update({"Account": res["account"], "Category": categorize(t["Description"], rules),
                      "Imported": date.today(), "File": path.name, "Key": f"{base}|{counts[base]}"})
    return results
