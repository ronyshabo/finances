"""The master workbook: sheet layout, importing records, and reading them back."""
from datetime import date, datetime
from pathlib import Path

from .gridcraft import GridCraft, GridCraftError, col_letter, serial_to_date

MARKER = "Personal Finance Master"

SHEETS = {
    "Income": ["Period Start", "Period End", "Source", "Shifts", "Hours", "Base Pay", "Tips",
               "Gross", "Tax Set Aside", "Net After Set Aside", "Imported", "File", "Key"],
    "Transactions": ["Date", "Account", "Description", "Amount", "Category", "Imported", "File", "Key"],
    "Accounts": ["Name", "Type", "Balance", "APR %", "Min Payment", "As Of", "Notes"],
    "Plan": ["Week", "Week Of", "Income (gross)", "Tax Set Aside", "Income (net)", "Allowance", "Bills", "To Cards", "Card Interest",
             "Card Balance", "Emergency Fund", "Invested", "Net Worth"],
    "Advice": ["Date", "For Month", "Topic", "Question", "Advice", "Model", "Cost $", "Key"],
}
DATE_COLS = {"Period Start", "Period End", "Imported", "Date", "As Of", "Week Of"}

DEFAULT_ACCOUNTS = [
    ["Checking", "checking", 0, 0, 0, None, "Debit / bank account"],
    ["Credit Card 1", "credit", 0, 0, 0, None, "Edit name, balance, APR, minimum"],
    ["Credit Card 2", "credit", 0, 0, 0, None, "Edit name, balance, APR, minimum"],
]


def _iso(d):
    return d.isoformat() if isinstance(d, (date, datetime)) else d


class MasterBook:
    def __init__(self, cfg):
        self.cfg = cfg
        self.path = Path(cfg["master_path"])
        self.gc = GridCraft(cfg["gridcraft_port"], cfg["flatpak_app_id"])

    # ---- connecting -----------------------------------------------------
    def _is_master_active(self):
        try:
            rows = self.gc.call("sheet.read", {"sheet": "Overview", "range": "A1"})["values"]
            return bool(rows) and rows[0] and rows[0][0] == MARKER
        except GridCraftError:
            return False

    def connect(self, log=print):
        """Make sure GridCraft is running with the master workbook active."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        launched = self.gc.ensure_running(self.path, self.cfg["launch_timeout_s"])
        log(f"Connected to GridCraft on port {self.gc.port}"
            + (" (launched it)" if launched else ""))
        if self._is_master_active():
            log(f"Master workbook active: {self.path}")
        elif self.path.exists():
            log(f"Opening {self.path}")
            self.gc.open(self.path)
            if not self._is_master_active():
                self._build_schema()
        else:
            log("No master workbook yet, creating one")
            if not launched:
                self.gc.call("file.new", {})
            self._build_schema()
        self._ensure_sheets()
        self.gc.save(self.path)
        log("Master workbook ready")

    def _build_schema(self):
        self.gc.try_call("sheet.rename", {"sheet": 0, "name": "Overview"})
        if not self.gc.sheet_exists("Overview"):
            self.gc.call("home.insertSheet", {"name": "Overview", "before": 0})
        self.gc.call("sheet.activate", {"sheet": "Overview"})
        self.gc.call("range.setValues", {"range": "A1", "values": [
            [MARKER],
            ["Managed by the Personal Finance app. Edit the Accounts sheet freely;"],
            ["Income and Transactions are appended by imports (the Key column prevents duplicates)."],
        ]})
        self.gc.try_call("home.cellStyle", {"range": "A1", "name": "Title"})

    def _ensure_sheets(self):
        for name, headers in SHEETS.items():
            if self.gc.ensure_sheet(name, headers) and name == "Accounts":
                self.gc.write_rows("Accounts", 2, DEFAULT_ACCOUNTS)

    # ---- reading --------------------------------------------------------
    def records(self, sheet):
        rows = self.gc.read_sheet(sheet)
        if not rows:
            return []
        headers = [str(h) if h is not None else "" for h in rows[0]]
        out = []
        for r in rows[1:]:
            if not any(v not in (None, "") for v in r):
                continue
            rec = {h: (r[i] if i < len(r) else None) for i, h in enumerate(headers) if h}
            for h in DATE_COLS & rec.keys():
                rec[h] = serial_to_date(rec[h])
            out.append(rec)
        return out

    # ---- writing --------------------------------------------------------
    def append(self, sheet, recs):
        """Append dict records, skipping ones whose Key is already present.

        Returns (added, skipped).
        """
        headers = SHEETS[sheet]
        existing = self.gc.read_sheet(sheet)
        key_idx = headers.index("Key") if "Key" in headers else None
        seen = {r[key_idx] for r in existing[1:] if key_idx is not None and len(r) > key_idx}
        new_rows = []
        for rec in recs:
            if key_idx is not None and rec.get("Key") in seen:
                continue
            seen.add(rec.get("Key"))
            new_rows.append([_iso(rec.get(h)) for h in headers])
        # The used range can include formatted-but-empty rows, so find the last row with data.
        last = max((i for i, r in enumerate(existing) if any(v not in (None, "") for v in r)), default=0)
        self.gc.write_rows(sheet, last + 2, new_rows)
        if new_rows:
            self.gc.save(self.path)
        return len(new_rows), len(recs) - len(new_rows)

    def replace(self, sheet, recs, headers=None):
        headers = headers or SHEETS[sheet]
        self.gc.clear_sheet_below_header(sheet, len(headers))
        self.gc.write_rows(sheet, 1, [headers])
        self.gc.write_rows(sheet, 2, [[_iso(r.get(h)) for h in headers] for r in recs])
        self.gc.save(self.path)
        return True

    def wrap_column(self, sheet, header, width=None):
        """Best-effort word wrap (and width) for a long-text column."""
        c = col_letter(SHEETS[sheet].index(header))
        self.gc.call("sheet.activate", {"sheet": sheet})
        self.gc.try_call("home.formatCells", {"range": f"{c}2:{c}5000",
                                              "style": {"align": {"wrap": True, "v": "Top"}}})
        if width:
            self.gc.try_call("home.columnWidth", {"cols": f"{c}:{c}", "chars": width})

    def format_dates(self, sheet):
        """Best-effort yyyy-mm-dd display for date columns."""
        headers = SHEETS[sheet]
        self.gc.call("sheet.activate", {"sheet": sheet})
        for i, h in enumerate(headers):
            if h in DATE_COLS:
                c = col_letter(i)
                self.gc.try_call("home.formatCells", {"range": f"{c}2:{c}5000",
                                                      "style": {"numFmt": "yyyy-mm-dd"}})
