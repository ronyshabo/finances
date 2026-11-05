"""Reads the BRB Fins data folder and does the owner-payout math for the planner app.

Recognized files (CSV or PDF, anywhere in the data folder's top level):
  sales     - POS daily export: "Day,Gross Sales,Return Sales,...,Total Sales"
  payroll   - barista payout tax summary: "Name,Year,Weeks Paid,..."
  bills     - monthly bills: "Bill,Monthly Amount[,Notes]"   (template made by the app)
  cogs      - accounting "Transactions by Account" PDF for Cost of Goods Sold
  balance   - accounting "Balance Sheet" PDF (used for cash in the bank)
"""
import csv
import datetime as dt
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

OWNERS_ON_PAYROLL = ["Rony", "Youssef"]      # owners who work barista shifts today
ALL_OWNERS = ["Youssef", "Rony", "Laila", "Tima"]
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
BILLS_TEMPLATE = "monthly-bills.csv"
DEFAULT_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


# ------------------------------------------------------------------ file records
@dataclass
class FileInfo:
    path: str
    kind: str            # sales | payroll | bills | cogs | balance | unknown
    covers: str = ""
    summary: str = ""
    status: str = "OK"


@dataclass
class Data:
    folder: str
    files: list = field(default_factory=list)
    daily: dict = field(default_factory=dict)        # date -> net sales
    payroll: dict = None                             # see _read_payroll
    bills: list = field(default_factory=list)        # (name, monthly amount, source file)
    cogs: dict = field(default_factory=dict)         # (year, ref) -> (date, vendor, desc, amount)
    cash: tuple = None                               # (as-of date, amount)
    warnings: list = field(default_factory=list)


# ------------------------------------------------------------------ detection
def pdf_text(path):
    try:
        out = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise RuntimeError("pdftotext is not installed (sudo apt install poppler-utils)")
    return out.stdout


def detect(path):
    """Return (kind, text-or-rows) for a file, or ('unknown', None)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        try:
            with open(path, newline="", encoding="utf-8-sig") as fh:
                rows = list(csv.reader(fh))
        except (OSError, UnicodeDecodeError):
            return "unknown", None
        head = [h.strip().lower() for h in (rows[0] if rows else [])]
        if head[:2] == ["day", "gross sales"] and "net sales" in head:
            return "sales", rows
        if head[:3] == ["name", "year", "weeks paid"]:
            return "payroll", rows
        if len(head) >= 2 and head[0] in ("bill", "name", "item") and "monthly" in head[1]:
            return "bills", rows
        return "unknown", None
    if ext == ".pdf":
        text = pdf_text(path)
        if "TRANSACTIONS BY ACCOUNT" in text and "Cost of Goods Sold" in text:
            return "cogs", text
        if "BALANCE SHEET" in text:
            return "balance", text
        return "unknown", None
    return "unknown", None


# ------------------------------------------------------------------ readers
def _money(s):
    s = s.replace("$", "").replace(",", "").strip()
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    v = float(s.strip("-()"))
    return -v if neg else v


def _read_sales(rows, data, info):
    head = [h.strip().lower() for h in rows[0]]
    i_day, i_net = head.index("day"), head.index("net sales")
    days = []
    for r in rows[1:]:
        if len(r) <= i_net or not r[i_day].strip():
            continue
        d = dt.date.fromisoformat(r[i_day].strip())
        data.daily[d] = float(r[i_net] or 0)
        days.append(d)
    if days:
        info.covers = f"{min(days):%b %d} – {max(days):%b %d, %Y}"
        info.summary = f"{len(days)} days, net ${sum(data.daily[d] for d in days):,.0f}"


def _read_payroll(rows, data, info):
    people, weeks = [], None
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        if r[0] == "TOTALS":
            weeks = float(r[2])
            continue
        if len(r) > 6 and re.fullmatch(r"\d{4}", r[1].strip() or ""):
            people.append(dict(name=r[0].strip().capitalize(), hours=float(r[4]),
                               base=float(r[5]), tips=float(r[6]), first=r[10], last=r[11]))
    if not people or not weeks:
        info.status = "Could not find people rows or the TOTALS row"
        return
    owners = {o: dict(hours=0.0, base=0.0, tips=0.0) for o in OWNERS_ON_PAYROLL}
    staff = dict(hours=0.0, base=0.0)
    for p in people:
        if p["name"] in owners:
            for k in ("hours", "base", "tips"):
                owners[p["name"]][k] += p[k]
        else:
            staff["hours"] += p["hours"]
            staff["base"] += p["base"]
    first = min(p["first"] for p in people)
    last = max(p["last"] for p in people)
    data.payroll = dict(
        weeks=weeks,
        owner_hours_wk={o: v["hours"] / weeks for o, v in owners.items()},
        owner_tips_wk={o: v["tips"] / weeks for o, v in owners.items()},
        staff_base_wk=staff["base"] / weeks,
        staff_rate=staff["base"] / staff["hours"] if staff["hours"] else 0.0,
        source=os.path.basename(info.path),
    )
    info.covers = f"weeks {first} – {last}"
    info.summary = (f"{len(people)} people, staff base ${data.payroll['staff_base_wk']:,.0f}/wk, "
                    f"owners {sum(data.payroll['owner_hours_wk'].values()):.0f} hrs/wk")


def _read_bills(rows, data, info):
    total, n = 0.0, 0
    for r in rows[1:]:
        if len(r) < 2 or not r[0].strip() or r[0].strip().startswith("#"):
            continue
        try:
            amt = _money(r[1]) if r[1].strip() else 0.0
        except ValueError:
            info.status = f"Amount not a number: {r[1]!r}"
            continue
        data.bills.append((r[0].strip(), amt, os.path.basename(info.path)))
        total += amt
        n += 1
    info.covers = "per month"
    info.summary = f"{n} bills, ${total:,.0f}/month"


_TX = re.compile(r"\s*(\d\d)\s+(\w{3})?\s*Transaction\s+(\d+)\s+(.+?)"
                 r"(?:\s{2,}|\s(?=[A-Z][a-z]+ Check Number))(.+?)Check Number:\s+(-?\$[\d,]+\.\d\d)")


def _read_cogs(text, data, info):
    m = re.search(r"from (\d\d) (\w{3}) (\d{4}) to (\d\d) (\w{3}) (\d{4})", text)
    if not m:
        info.status = "Could not find the report period"
        return
    start_month, start_year = MONTHS[m.group(2)], int(m.group(3))
    lines = text.split("\n")
    total, n = 0.0, 0
    dates = []
    for i, line in enumerate(lines):
        t = _TX.match(line)
        if not t:
            continue
        day, mon, ref, desc, vendor, amt = t.groups()
        if not mon:
            mon = re.match(r"\s*(\w{3})", lines[i + 1]).group(1)
        year = start_year + (1 if MONTHS[mon] < start_month else 0)
        for j in (1, 2):
            seg = lines[i + j][:46].strip() if i + j < len(lines) else ""
            seg = re.sub(r"^\d{4}\s*|^(" + "|".join(MONTHS) + r")\s*", "", seg).strip()
            if seg and not seg.startswith("Date") and "Transaction" not in seg:
                desc += " " + seg
        desc = re.sub(r"\s+", " ", desc).strip()
        vendor = vendor.strip()
        if "TRANSFER" in desc.upper():
            vendor = "Transfer"
        elif vendor == "debit":
            vendor = "H E B"
        d = dt.date(year, MONTHS[mon], int(day))
        data.cogs[(year, int(ref))] = (d, vendor, desc, _money(amt))
        total += _money(amt)
        dates.append(d)
        n += 1
    reported = re.search(r"Total for Cost of Goods Sold\s+(-?\$[\d,]+\.\d\d)", text)
    if reported and abs(_money(reported.group(1)) - total) > 0.01:
        info.status = f"Read ${total:,.2f} but report total is {reported.group(1)}"
    if dates:
        info.covers = f"{min(dates):%b %d} – {max(dates):%b %d, %Y}"
    info.summary = f"{n} transactions, ${total:,.0f}"
    unc = re.search(r"(\d+) uncategorized transactions", text)
    if unc:
        info.summary += f" ({unc.group(1)} uncategorized in books)"


def _read_balance(text, data, info):
    m = re.search(r"As of (\d\d \w{3} \d{4})", text)
    cash = re.search(r"(?:Checking|Savings)\s+(-?\$[\d,]+\.\d\d)", text)
    if not (m and cash):
        info.status = "Could not find the date or the checking balance"
        return
    d = dt.datetime.strptime(m.group(1), "%d %b %Y").date()
    if data.cash is None or d > data.cash[0]:
        data.cash = (d, _money(cash.group(1)))
    info.covers = f"as of {d:%b %d, %Y}"
    info.summary = f"checking {cash.group(1)}"


READERS = {"sales": _read_sales, "payroll": _read_payroll, "bills": _read_bills,
           "cogs": _read_cogs, "balance": _read_balance}


def load(folder):
    data = Data(folder=folder)
    paths = sorted((os.path.join(folder, f) for f in os.listdir(folder)
                    if f.lower().endswith((".csv", ".pdf"))), key=os.path.getmtime)
    payroll_files = []
    for p in paths:
        try:
            kind, content = detect(p)
        except RuntimeError as e:
            data.warnings.append(str(e))
            continue
        info = FileInfo(p, kind)
        if kind == "unknown":
            info.status = "Not recognized — ignored"
        elif kind == "payroll":
            payroll_files.append((info, content))   # only the newest one is used
        else:
            try:
                READERS[kind](content, data, info)
            except Exception as e:                   # keep going; show the problem in the Files tab
                info.status = f"Error reading: {e}"
        data.files.append(info)
    for k, (info, content) in enumerate(payroll_files):
        if k == len(payroll_files) - 1:
            try:
                _read_payroll(content, data, info)
            except Exception as e:
                info.status = f"Error reading: {e}"
        else:
            info.status = "Older payroll file — not used"
    if not data.daily:
        data.warnings.append("No sales files found.")
    if data.payroll is None:
        data.warnings.append("No payroll summary found — staff pay and owner hours are 0.")
    if not data.cogs:
        data.warnings.append("No cost-of-goods report found — using the default COGS %.")
    if not data.bills:
        data.warnings.append("No bills file — rent and other bills are $0. Use 'Make bills template'.")
    return data


def import_files(paths, folder, confirm_overwrite):
    """Copy recognized files into the data folder. Returns (copied, skipped[(name, reason)])."""
    copied, skipped = [], []
    for src in paths:
        name = os.path.basename(src)
        try:
            kind, _ = detect(src)
        except RuntimeError as e:
            skipped.append((name, str(e)))
            continue
        if kind == "unknown":
            skipped.append((name, "not a recognized sales, payroll, bills, COGS or balance-sheet file"))
            continue
        dest = os.path.join(folder, name)
        if os.path.abspath(src) == os.path.abspath(dest):
            skipped.append((name, "already in the folder"))
            continue
        if os.path.exists(dest) and not confirm_overwrite(name):
            skipped.append((name, "kept the existing file"))
            continue
        shutil.copy2(src, dest)
        copied.append(name)
    return copied, skipped


def write_bills_template(folder):
    path = os.path.join(folder, BILLS_TEMPLATE)
    if os.path.exists(path):
        return path, False
    rows = [["Bill", "Monthly Amount", "Notes"],
            ["Rent incl. utilities", "2700", "Today. Change to 3000 if the new deal goes through"],
            ["Insurance", "", ""], ["Internet & phone", "", ""], ["POS / software", "", ""],
            ["Loan or lease payments", "", ""], ["Repairs & maintenance", "", ""],
            ["Licenses & permits", "", ""], ["Accounting", "", ""], ["Marketing", "", ""], ["Other", "", ""]]
    with open(path, "w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    return path, True


# ------------------------------------------------------------------ derived numbers
def sales_weeks(data, today=None):
    """Full 7-day weeks starting from the first sales day; the week containing today is left out."""
    today = today or dt.date.today()
    if not data.daily:
        return []
    start, last = min(data.daily), max(data.daily)
    weeks = []
    while start + dt.timedelta(days=6) <= last:
        days = [start + dt.timedelta(days=k) for k in range(7)]
        if today not in days and all(d in data.daily for d in days):
            weeks.append((start, sum(data.daily[d] for d in days)))
        start += dt.timedelta(days=7)
    return weeks


def cogs_rate(data, include_transfers=True):
    """COGS ÷ net sales over whole months the books cover. The latest month is dropped unless its
    last transaction is on/after the 25th (books usually lag). Returns (rate, label) or (None, reason)."""
    if not data.cogs or not data.daily:
        return None, "no data"
    tx = list(data.cogs.values())
    latest = max(t[0] for t in tx)
    months = sorted({(t[0].year, t[0].month) for t in tx})
    if latest.day < 25 and len(months) > 1:
        months = months[:-1]
    keep = []
    for y, m in months:
        m_start = dt.date(y, m, 1)
        n_days = (dt.date(y + m // 12, m % 12 + 1, 1) - m_start).days
        if all(m_start + dt.timedelta(days=k) in data.daily for k in range(n_days)):
            keep.append((y, m))
    if not keep:
        return None, "no month has both full sales and cost-of-goods data"
    inm = lambda d: (d.year, d.month) in keep
    cost = sum(a for d, v, _, a in tx if inm(d) and (include_transfers or v != "Transfer"))
    sales = sum(v for d, v in data.daily.items() if inm(d))
    y0, m0 = keep[0]
    y1, m1 = keep[-1]
    label = f"{dt.date(y0, m0, 1):%b} – {dt.date(y1, m1, 1):%b %Y}"
    return (cost / sales if sales else None), label


def compute(data, growth_wk, price, loss=0.02, fee=0.03, rate=None, include_transfers=True,
            basis="Average of full weeks", default_cogs=0.30, owners=4):
    """Weekly money flow once Rony & Youssef have stepped out of shifts."""
    weeks = sales_weeks(data)
    vals = [v for _, v in weeks]
    if not vals:
        base = 0.0
    elif basis == "Last 4 full weeks":
        base = sum(vals[-4:]) / len(vals[-4:])
    elif basis == "Lowest week":
        base = min(vals)
    else:
        base = sum(vals) / len(vals)
    cogs_pct, cogs_label = cogs_rate(data, include_transfers)
    if cogs_pct is None:
        cogs_pct, cogs_label = default_cogs, f"default {default_cogs:.0%} ({cogs_label})"
    pr = data.payroll or dict(owner_hours_wk={}, staff_base_wk=0.0, staff_rate=0.0)
    rate = pr["staff_rate"] if rate is None else rate
    owner_hours = sum(pr["owner_hours_wk"].values())
    bills_m = sum(b[1] for b in data.bills)

    sales = base * (1 + price) * (1 - loss)
    out = dict(
        cogs=base * (1 - loss) * cogs_pct,
        fees=sales * fee,
        bills=bills_m * 12 / 52,
        staff=pr["staff_base_wk"],
        cover=owner_hours * rate,
    )
    before_owners = sales - sum(out.values())
    pool = before_owners - growth_wk
    return dict(
        base_sales=base, sales=sales, cogs_pct=cogs_pct, cogs_label=cogs_label, weeks=len(vals),
        owner_hours=owner_hours, rate=rate, bills_m=bills_m, costs=out,
        before_owners=before_owners, growth=growth_wk, pool=pool,
        per_owner=max(0.0, pool) / owners, owners=owners,
    )


if __name__ == "__main__":
    import sys
    d = load(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FOLDER)
    for f in d.files:
        print(f"{f.kind:8} {os.path.basename(f.path):55} {f.covers:28} {f.summary}  [{f.status}]")
    print("warnings:", d.warnings)
    r = compute(d, 200, 0.05)
    print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items() if k != "costs"})
    print({k: round(v, 2) for k, v in r["costs"].items()})
