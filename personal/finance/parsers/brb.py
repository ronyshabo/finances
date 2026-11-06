"""Parser for BRB earnings statements (the text summary sent for a pay period)."""
import re
from datetime import date

from . import read_text

_MONEY = r"\$?\s*(-?[\d,]+(?:\.\d+)?)"
_FIELDS = {
    "Shifts": r"Shifts:\s*(\d+)",
    "Hours": r"Hours:\s*([\d.,]+)",
    "Base Pay": r"Base pay:\s*" + _MONEY,
    "Tips": r"Tips:\s*" + _MONEY,
    "Gross": r"Gross earnings:\s*" + _MONEY,
    "Tax Set Aside": r"Set aside:\s*" + _MONEY,
    "Net After Set Aside": r"Left after setting that aside:\s*" + _MONEY,
}


def _num(s):
    return float(s.replace(",", ""))


def looks_like_brb(text):
    return "Earnings Statement" in text and "Gross earnings" in text


def parse_text(text, filename=""):
    clean = text.replace("*", "").replace("_", " ")
    m = re.search(r"Period:\s*(\d{4}-\d{2}-\d{2})\s*to\s*(\d{4}-\d{2}-\d{2})", clean)
    if not m:
        raise ValueError("Couldn't find 'Period: YYYY-MM-DD to YYYY-MM-DD' in the statement")
    start, end = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
    rec = {"Period Start": start, "Period End": end, "Source": "BRB", "File": filename,
           "Imported": date.today()}
    for field, pat in _FIELDS.items():
        fm = re.search(pat, clean, re.IGNORECASE)
        rec[field] = _num(fm.group(1)) if fm else None
    if rec["Gross"] is None:
        rec["Gross"] = (rec["Base Pay"] or 0) + (rec["Tips"] or 0)
    if rec["Net After Set Aside"] is None and rec["Tax Set Aside"] is not None:
        rec["Net After Set Aside"] = round(rec["Gross"] - rec["Tax Set Aside"], 2)
    rec["Key"] = f"BRB|{start}|{end}"
    return rec


def parse_file(path):
    return parse_text(read_text(path), path.name)
