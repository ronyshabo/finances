"""Tkinter front end: dashboard, imports, accounts and the plan simulator."""
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from finance import advisor, business, config, inbox, planner
from finance.gridcraft import GridCraftError
from finance.master import MasterBook
from finance.parsers import bank, brb

GREEN, RED, AMBER = "#1f9d55", "#c0392b", "#d68910"


def money(v):
    return f"-${-v:,.2f}" if v < 0 else f"${v:,.2f}"


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class App(tk.Tk):
    def __init__(self):
        super().__init__(className="PersonalFinance")  # matches StartupWMClass in the .desktop file
        self.title("Personal Finance")
        icon = config.PROJECT_DIR / "assets" / "icon.png"
        if icon.exists():
            self._icon = tk.PhotoImage(file=str(icon))
            self.iconphoto(True, self._icon)
        self.geometry("1280x820")
        self.minsize(1000, 680)
        self.cfg = config.load()
        self.book = MasterBook(self.cfg)
        self.income, self.transactions, self.accounts = [], [], []
        self._sim_job = None
        self.biz = None  # BRB shop snapshot from ../brb, see load_business

        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Big.TLabel", font=("TkDefaultFont", 15, "bold"))
        style.configure("Head.TLabel", font=("TkDefaultFont", 11, "bold"))
        line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        style.configure("Treeview", rowheight=line + 8)

        self._build_statusbar()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._build_dashboard()
        self._build_import()
        self._build_accounts()
        self._build_simulator()
        self._build_advisor()
        self.after(100, self.connect)
        self.after(200, self.load_business)

    # ================================================================ status
    def _build_statusbar(self):
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill="x")
        self.status_dot = tk.Label(bar, text="●", fg=AMBER, font=("TkDefaultFont", 14))
        self.status_dot.pack(side="left")
        self.status_lbl = ttk.Label(bar, text="Connecting to GridCraft…")
        self.status_lbl.pack(side="left", padx=6)
        ttk.Button(bar, text="Reconnect", command=self.connect).pack(side="right")
        ttk.Button(bar, text="Reload data", command=self.reload).pack(side="right", padx=6)
        ttk.Label(bar, text=f"Master: {self.book.path}", foreground="#666").pack(side="right", padx=12)

    def set_status(self, text, color):
        self.status_lbl.config(text=text)
        self.status_dot.config(fg=color)

    def connect(self):
        self.set_status("Connecting to GridCraft…", AMBER)

        def work():
            try:
                self.book.connect(log=lambda m: self.after(0, self.log, m))
                self.after(0, self._on_connected)
            except (GridCraftError, OSError) as e:
                self.after(0, self._on_connect_failed, e)
        threading.Thread(target=work, daemon=True).start()

    def _on_connected(self):
        self.set_status(f"Connected to GridCraft (port {self.book.gc.port})", GREEN)
        self.reload()
        if inbox.pending():
            self.process_inbox()

    def _on_connect_failed(self, e):
        self.set_status(f"GridCraft not connected: {e}", RED)
        self.log(f"Connection failed: {e}")
        self.log("If GridCraft is minimized, restore it and close any dialog in it, then press Reconnect.")

    def gc_guard(self, fn, *args):
        """Run a workbook operation, showing connection problems instead of crashing."""
        try:
            return fn(*args)
        except GridCraftError as e:
            self.set_status(f"GridCraft error: {e}", RED)
            messagebox.showerror("GridCraft", f"{e}\n\nGridCraft only answers while its window is on screen. "
                                              "If it's minimized, restore it (and close any dialog in it), "
                                              "then press Reconnect.")
            return None

    def reload(self):
        def load():
            self.income = self.book.records("Income")
            self.transactions = self.book.records("Transactions")
            self.accounts = self.book.records("Accounts")
            return True
        if self.gc_guard(load):
            self.refresh_accounts_view()
            self.apply_income_defaults()
            self.refresh_dashboard()
            self.schedule_sim()

    # ============================================================= dashboard
    def _build_dashboard(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="Dashboard")
        top = ttk.Frame(tab)
        top.pack(fill="x")
        self.tiles = {}
        for key, label in [("weekly", "BRB avg weekly gross"), ("net", "BRB avg after tax"),
                           ("payout", "Shop owner payout/wk"), ("debt", "Credit card debt"), ("cash", "Checking"),
                           ("debtfree", "Debt-free by")]:
            f = ttk.Frame(top, padding=10, relief="groove")
            f.pack(side="left", fill="x", expand=True, padx=4)
            ttk.Label(f, text=label, foreground="#555").pack(anchor="w")
            v = ttk.Label(f, text="–", style="Big.TLabel")
            v.pack(anchor="w")
            self.tiles[key] = v

        body = ttk.Frame(tab)
        body.pack(fill="both", expand=True, pady=10)
        left = ttk.Frame(body)
        left.pack(side="left", fill="y", padx=(0, 12))
        ttk.Label(left, text="This week's plan", style="Head.TLabel").pack(anchor="w")
        self.week_plan = tk.Text(left, width=46, height=14, relief="flat", wrap="word",
                                 font=("TkDefaultFont", 11))
        self.week_plan.pack(fill="y", pady=4)
        ttk.Label(left, text="Income statements", style="Head.TLabel").pack(anchor="w", pady=(8, 0))
        self.income_lbl = ttk.Label(left, text="", justify="left")
        self.income_lbl.pack(anchor="w")

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        ttk.Label(right, text="Spending by category (last 30 days of transactions)",
                  style="Head.TLabel").pack(anchor="w")
        self.dash_fig = Figure(figsize=(6, 4), dpi=96)
        self.dash_canvas = FigureCanvasTkAgg(self.dash_fig, master=right)
        self.dash_canvas.get_tk_widget().pack(fill="both", expand=True)

    def refresh_dashboard(self):
        wk, tax, weeks = planner.weekly_income_from_records(self.income)
        self.tiles["weekly"].config(text=money(wk) if wk else "–")
        if wk:
            self.tiles["net"].config(text=money(wk * (1 - (tax or self.cfg["plan"]["tax_set_aside_pct"]) / 100)))
        cards = [a for a in self.accounts if str(a.get("Type", "")).lower() == "credit"]
        checking = sum(_num(a.get("Balance")) for a in self.accounts
                       if str(a.get("Type", "")).lower() == "checking")
        self.tiles["debt"].config(text=money(sum(_num(c.get("Balance")) for c in cards)))
        self.tiles["cash"].config(text=money(checking))
        lines = []
        for r in sorted(self.income, key=lambda r: r.get("Period End") or date.min, reverse=True)[:5]:
            lines.append(f"{r.get('Period Start')} → {r.get('Period End')}:  gross {money(_num(r.get('Gross')))}"
                         f",  tips {money(_num(r.get('Tips')))}")
        if weeks:
            lines.append(f"Covers {weeks:.1f} weeks; tax set-aside ≈ {tax:.1f}%")
        self.income_lbl.config(text="\n".join(lines) or "No income imported yet.")

        self.dash_fig.clear()
        ax = self.dash_fig.add_subplot(111)
        dated = [t for t in self.transactions if t.get("Date")]
        if dated:
            last = max(t["Date"] for t in dated)
            by_cat = defaultdict(float)
            for t in dated:
                amt = _num(t.get("Amount"))
                if t["Date"] > last - timedelta(days=30) and amt < 0 and t.get("Category") != "Card Payment":
                    by_cat[t.get("Category") or "Other"] += -amt
            items = sorted(by_cat.items(), key=lambda kv: kv[1])
            if items:
                ax.barh([k for k, _ in items], [v for _, v in items], color="#2a78b8")
                for i, (_, v) in enumerate(items):
                    ax.text(v, i, f" ${v:,.0f}", va="center", fontsize=9)
                ax.spines[["top", "right"]].set_visible(False)
        if not ax.has_data():
            ax.text(0.5, 0.5, "Import bank statements to see spending", ha="center", va="center",
                    transform=ax.transAxes, color="#777")
            ax.set_axis_off()
        self.dash_fig.tight_layout()
        self.dash_canvas.draw_idle()

    # ================================================================ import
    def _build_import(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="Import")
        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Button(row, text="Process inbox", command=self.process_inbox).pack(side="left")
        ttk.Button(row, text="Open inbox folder", command=self.open_inbox).pack(side="left", padx=6)
        ttk.Button(row, text="Add files…", command=self.import_files).pack(side="left")
        ttk.Button(row, text="Paste BRB statement…", command=self.paste_brb).pack(side="left", padx=6)
        ttk.Label(tab, text=f"Drop statements into {inbox.INBOX} — they're identified by file name, imported "
                            "to GridCraft, then moved to archive/.", foreground="#555",
                  wraplength=1100).pack(anchor="w", pady=4)
        ttk.Label(tab, text="Waiting in inbox", style="Head.TLabel").pack(anchor="w", pady=(6, 2))
        self.inbox_tree = ttk.Treeview(tab, columns=("File", "Identified as", "By"), show="headings", height=5)
        for c, w in (("File", 320), ("Identified as", 360), ("By", 120)):
            self.inbox_tree.heading(c, text=c)
            self.inbox_tree.column(c, width=w)
        self.inbox_tree.pack(fill="x")
        ttk.Label(tab, text="Recent transactions", style="Head.TLabel").pack(anchor="w", pady=(12, 2))
        cols = ("Date", "Account", "Description", "Amount", "Category")
        self.txn_tree = ttk.Treeview(tab, columns=cols, show="headings", height=10)
        for c, w in zip(cols, (90, 200, 400, 90, 120)):
            self.txn_tree.heading(c, text=c)
            self.txn_tree.column(c, width=w, anchor="e" if c == "Amount" else "w")
        self.txn_tree.pack(fill="both", expand=True)
        ttk.Label(tab, text="Log", style="Head.TLabel").pack(anchor="w", pady=(10, 2))
        self.log_box = tk.Text(tab, height=8, state="disabled", font=("TkFixedFont", 9))
        self.log_box.pack(fill="x")
        self.refresh_inbox_view()

    def log(self, msg):
        self.log_box.config(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def refresh_inbox_view(self):
        self.inbox_tree.delete(*self.inbox_tree.get_children())
        for p in inbox.pending():
            info = inbox.identify(p, self.cfg)
            self.inbox_tree.insert("", "end", values=(p.name, info.label, info.matched_by))

    def open_inbox(self):
        inbox.INBOX.mkdir(exist_ok=True)
        subprocess.Popen(["xdg-open", str(inbox.INBOX)])

    def _refresh_txn_tree(self):
        self.txn_tree.delete(*self.txn_tree.get_children())
        recent = sorted(self.transactions, key=lambda t: t.get("Date") or date.min, reverse=True)[:300]
        for t in recent:
            self.txn_tree.insert("", "end", values=(t.get("Date"), t.get("Account"), t.get("Description"),
                                                    money(_num(t.get("Amount"))), t.get("Category")))

    def import_files(self):
        paths = filedialog.askopenfilenames(
            title="Choose statements",
            initialdir=str(Path.home() / "Downloads"),
            filetypes=[("Statements", "*.pdf *.csv *.txt *.md"), ("All files", "*")])
        inbox.INBOX.mkdir(exist_ok=True)
        for p in map(Path, paths):
            shutil.copy2(p, inbox.INBOX / p.name)
        if paths:
            self.process_inbox()

    def process_inbox(self):
        files = inbox.pending()
        if not files:
            self.log("Inbox is empty.")
            return
        if not self.book.gc.connected:
            self.log(f"{len(files)} file(s) waiting in the inbox; connect to GridCraft to import them.")
            return
        self.log(f"Processing {len(files)} file(s) from the inbox…")
        accounts = [dict(a) for a in self.accounts]
        for p in files:
            info = inbox.identify(p, self.cfg)
            try:
                ok = self._import_one(info, accounts)
            except GridCraftError as e:
                self.set_status(f"GridCraft error: {e}", RED)
                self.log(f"✗ {p.name}: {e} (left in inbox; Reconnect and Process inbox again)")
                break
            except Exception as e:  # a bad file shouldn't stop the batch
                self.log(f"✗ {p.name}: {e} (left in inbox)")
                continue
            if ok:
                dest = inbox.archive(info, *ok)
                self.log(f"  archived as {dest.relative_to(inbox.ARCHIVE.parent)}")
        if accounts != self.accounts and self.gc_guard(self.book.replace, "Accounts", accounts):
            self.gc_guard(self.book.format_dates, "Accounts")
        self.refresh_inbox_view()
        self.reload()

    def _import_one(self, info, accounts):
        """Import one file. Returns (period, account) for its archive name, or None."""
        p = info.path
        if info.kind == "brb":
            rec = brb.parse_file(p)
            if not self._save_income(rec):
                return None
            return rec["Period End"].strftime("%Y-%m"), "BRB"
        results = bank.parse_file(p, rules=self.cfg["category_rules"], card_names=self.cfg["cards"])
        layout = results[0]["layout"] if results else "generic"
        is_card = any(r["kind"] == "credit" for r in results)
        if (info.kind == "card") != is_card and layout != "generic":
            self.log(f"  ! {p.name}: name says {info.kind}, contents look like "
                     f"{'a card' if is_card else 'a bank'} statement — using the contents")
        if layout in ("generic", "csv"):
            names = [a.get("Name") for a in accounts if a.get("Name")]
            account = info.account or self.ask_account(p.name, names)
            if not account:
                return None
            for r in results:
                r["account"] = account
                for t in r["transactions"]:
                    t["Account"] = account
                    t["Key"] = account + t["Key"][t["Key"].index("|"):]
        txns = [t for r in results for t in r["transactions"]]
        res = self.gc_guard(self.book.append, "Transactions", txns)
        if res is None:
            return None
        self.gc_guard(self.book.format_dates, "Transactions")
        self.log(f"✓ {p.name} [{info.label}]: {res[0]} new transactions, {res[1]} already imported")
        period = info.period
        for r in results:
            summ = r["summary"]
            period = period or (summ.get("period_end").strftime("%Y-%m") if summ.get("period_end") else "")
            if summ.get("ending_balance") is not None and (r["transactions"] or summ["ending_balance"]):
                self._update_account(accounts, r["account"], r["kind"], summ)
        if not txns:
            self.log(f"  ! no transactions recognised in {p.name}; its layout may need a custom parser")
        main = info.account or (self.cfg["bank_name"] if layout == "capital_one_bank" else results[0]["account"])
        return period, main

    def paste_brb(self):
        win = tk.Toplevel(self)
        win.title("Paste BRB earnings statement")
        txt = tk.Text(win, width=80, height=24)
        txt.pack(fill="both", expand=True, padx=8, pady=8)

        def ok():
            try:
                rec = brb.parse_text(txt.get("1.0", "end"), "pasted")
            except ValueError as e:
                messagebox.showerror("BRB statement", str(e), parent=win)
                return
            # keep a copy of the pasted statement alongside the archived files
            dest = inbox.ARCHIVE / "brb"
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f"{rec['Period End']:%Y-%m}_brb_{rec['Period Start']}_to_{rec['Period End']}.txt").write_text(
                txt.get("1.0", "end"))
            self._save_income(rec)
            win.destroy()
            self.reload()
        ttk.Button(win, text="Import", command=ok).pack(pady=(0, 8))

    def _save_income(self, rec):
        res = self.gc_guard(self.book.append, "Income", [rec])
        if res:
            self.gc_guard(self.book.format_dates, "Income")
            self.log(f"✓ BRB {rec['Period Start']} → {rec['Period End']}: gross {money(rec['Gross'])}"
                     + ("" if res[0] else " (already imported)"))
        return res

    def ask_account(self, filename, names):
        win = tk.Toplevel(self)
        win.title("Which account?")
        win.transient(self)
        ttk.Label(win, text=f"Which account is “{filename}” for?", padding=10).pack()
        var = tk.StringVar(value=names[0] if names else "")
        ttk.Combobox(win, textvariable=var, values=names, width=30).pack(padx=10)
        out = {}
        ttk.Button(win, text="OK", command=lambda: (out.setdefault("v", var.get().strip()), win.destroy())
                   ).pack(pady=10)
        win.grab_set()
        self.wait_window(win)
        return out.get("v")

    PLACEHOLDERS = {"Checking": "checking", "Credit Card 1": "credit", "Credit Card 2": "credit"}

    def _update_account(self, accounts, name, kind, summ):
        """Set an account's balance/APR/minimum from a statement, unless a newer one was already applied."""
        acct = next((a for a in accounts if a.get("Name") == name), None)
        if acct is None:  # take over an untouched placeholder row of the same type
            acct = next((a for a in accounts if self.PLACEHOLDERS.get(a.get("Name")) == kind
                         and not _num(a.get("Balance"))), None)
            if acct is None:
                acct = {}
                accounts.append(acct)
            acct.update({"Name": name, "Type": kind, "Notes": ""})
        as_of = summ.get("period_end") or date.today()
        if acct.get("As Of") and acct["As Of"] > as_of:
            self.log(f"  {name}: kept the newer balance from {acct['As Of']}")
            return
        acct["Balance"] = abs(summ["ending_balance"]) if kind == "credit" else summ["ending_balance"]
        acct["As Of"] = as_of
        if summ.get("apr"):
            acct["APR %"] = summ["apr"]
        if summ.get("apy") is not None:
            acct["APR %"] = summ["apy"]
        if summ.get("min_payment") is not None:
            acct["Min Payment"] = summ["min_payment"]
        if summ.get("credit_limit"):
            acct["Notes"] = f"Credit limit {money(summ['credit_limit'])}"
        self.log(f"  {name}: balance {money(_num(acct['Balance']))} as of {as_of}"
                 + (f", APR {acct['APR %']}%" if kind == "credit" and acct.get("APR %") else "")
                 + (f", min {money(_num(acct.get('Min Payment')))}/mo" if acct.get("Min Payment") else ""))

    # ============================================================== accounts
    def _build_accounts(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="Accounts")
        ttk.Label(tab, text="Checking and credit cards (stored on the Accounts sheet). "
                            "Select a row to edit it.", foreground="#555").pack(anchor="w")
        cols = ("Name", "Type", "Balance", "APR %", "Min Payment", "As Of")
        self.acct_tree = ttk.Treeview(tab, columns=cols, show="headings", height=8)
        for c in cols:
            self.acct_tree.heading(c, text=c)
            self.acct_tree.column(c, width=280 if c == "Name" else 140)
        self.acct_tree.pack(fill="x", pady=8)
        self.acct_tree.bind("<<TreeviewSelect>>", self._on_acct_select)

        form = ttk.Frame(tab)
        form.pack(fill="x")
        self.acct_vars = {}
        for i, (key, label) in enumerate([("Name", "Name"), ("Type", "Type"), ("Balance", "Balance owed / in account"),
                                          ("APR %", "APR %"), ("Min Payment", "Monthly minimum")]):
            ttk.Label(form, text=label).grid(row=0, column=i, sticky="w", padx=4)
            var = tk.StringVar()
            if key == "Type":
                ttk.Combobox(form, textvariable=var, values=["checking", "credit", "savings", "investment"],
                             width=12).grid(row=1, column=i, padx=4)
            else:
                ttk.Entry(form, textvariable=var, width=18).grid(row=1, column=i, padx=4)
            self.acct_vars[key] = var
        btns = ttk.Frame(tab)
        btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="Save / update account", command=self.save_account).pack(side="left")
        ttk.Button(btns, text="Delete selected", command=self.delete_account).pack(side="left", padx=6)

    def refresh_accounts_view(self):
        self.acct_tree.delete(*self.acct_tree.get_children())
        for a in self.accounts:
            self.acct_tree.insert("", "end", values=(a.get("Name"), a.get("Type"), money(_num(a.get("Balance"))),
                                                     a.get("APR %"), money(_num(a.get("Min Payment"))),
                                                     a.get("As Of") or ""))
        self._refresh_txn_tree()
        self._rebuild_card_sliders()

    def _on_acct_select(self, _e):
        sel = self.acct_tree.selection()
        if not sel:
            return
        a = self.accounts[self.acct_tree.index(sel[0])]
        for k, var in self.acct_vars.items():
            v = a.get(k)
            var.set("" if v is None else v)

    def save_account(self):
        new = {k: v.get().strip() for k, v in self.acct_vars.items()}
        if not new["Name"]:
            return
        for k in ("Balance", "APR %", "Min Payment"):
            new[k] = _num(new[k].replace("$", "").replace(",", ""))
        new["As Of"] = date.today()
        accts = [dict(a) for a in self.accounts]
        existing = next((a for a in accts if a.get("Name") == new["Name"]), None)
        if existing:
            existing.update(new)
        else:
            sel = self.acct_tree.selection()
            if sel:  # renaming the selected account
                accts[self.acct_tree.index(sel[0])].update(new)
            else:
                accts.append(new)
        if self.gc_guard(self.book.replace, "Accounts", accts):
            self.gc_guard(self.book.format_dates, "Accounts")
            self.reload()

    def delete_account(self):
        sel = self.acct_tree.selection()
        if not sel or not messagebox.askyesno("Delete", "Remove this account from the Accounts sheet?"):
            return
        accts = [a for i, a in enumerate(self.accounts) if i != self.acct_tree.index(sel[0])]
        self.gc_guard(self.book.replace, "Accounts", accts)
        self.reload()

    # ============================================================= simulator
    def _build_simulator(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Plan & Simulator")
        side_outer = ttk.Frame(tab)
        side_outer.pack(side="left", fill="y")
        canvas = tk.Canvas(side_outer, width=340, highlightthickness=0)
        scroll = ttk.Scrollbar(side_outer, orient="vertical", command=canvas.yview)
        side = ttk.Frame(canvas)
        side.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=side, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="y")
        scroll.pack(side="left", fill="y")

        p = self.cfg["plan"]
        self.vars = {}
        ttk.Label(side, text="Income", style="Head.TLabel").pack(anchor="w", pady=(4, 0))
        self._slider(side, "weekly_gross", "Weekly pay, before tax ($)", 0, 1500, p["fixed_weekly_pay"], 5)
        self.raise_on = tk.BooleanVar(value=True)
        ttk.Checkbutton(side, text="Pay raise coming", variable=self.raise_on,
                        command=self.schedule_sim).pack(anchor="w")
        self._slider(side, "raise_to", "   new weekly pay ($)", 0, 1500, p["raise_to"], 5)
        self._slider(side, "raise_in_weeks", "   starts in (weeks)", 0, 104, p["raise_in_weeks"], 1)
        self.brb_ref = ttk.Label(side, text="", foreground="#666")
        self.brb_ref.pack(anchor="w")
        bz = ttk.Frame(side, padding=(0, 4))
        bz.pack(fill="x")
        self.biz_lbl = ttk.Label(bz, text="Shop owner payout: loading…", foreground="#666", wraplength=320,
                                 justify="left")
        self.biz_lbl.pack(anchor="w")
        bb = ttk.Frame(bz)
        bb.pack(anchor="w", pady=2)
        self.biz_use = ttk.Button(bb, text="Use as the raise", command=self.use_payout, state="disabled")
        self.biz_use.pack(side="left")
        ttk.Button(bb, text="Open BRB Planner", command=self.open_brb).pack(side="left", padx=4)
        self._slider(side, "income_change_pct", "What if income changes (%)", -50, 200,
                     p["income_change_pct"], 5)
        self._slider(side, "tax_set_aside_pct", "Tax set-aside (%)", 0, 40, p["tax_set_aside_pct"], 0.5)
        ttk.Label(side, text="Spending", style="Head.TLabel").pack(anchor="w", pady=(8, 0))
        self._slider(side, "weekly_allowance", "Weekly allowance ($)", 0, 500, p["weekly_allowance"], 5)
        self._slider(side, "weekly_bills", "Fixed bills per week ($)", 0, 800, p["weekly_bills"], 5)
        ttk.Label(side, text="Credit cards", style="Head.TLabel").pack(anchor="w", pady=(8, 0))
        self.strategy = tk.StringVar(value=p["strategy"])
        sf = ttk.Frame(side)
        sf.pack(anchor="w")
        ttk.Radiobutton(sf, text="Avalanche (highest APR first)", value="avalanche", variable=self.strategy,
                        command=self.schedule_sim).pack(anchor="w")
        ttk.Radiobutton(sf, text="Snowball (smallest balance first)", value="snowball", variable=self.strategy,
                        command=self.schedule_sim).pack(anchor="w")
        self.card_frame = ttk.Frame(side)
        self.card_frame.pack(fill="x")
        self.card_vars = {}
        ttk.Label(side, text="Saving & investing", style="Head.TLabel").pack(anchor="w", pady=(8, 0))
        self.use_buffer = tk.BooleanVar(value=True)
        ttk.Checkbutton(side, text="Keep a starter cash buffer while paying cards", variable=self.use_buffer,
                        command=self.schedule_sim).pack(anchor="w")
        self._slider(side, "starter_buffer", "Starter buffer ($)", 0, 2000, 300, 50)
        self._slider(side, "emergency_fund_target", "Emergency fund goal ($)", 0, 15000,
                     p["emergency_fund_target"], 100)
        self._slider(side, "invest_pct_after_debt", "Invest % of spare after debt", 0, 100,
                     p["invest_pct_after_debt"], 5)
        self._slider(side, "invest_return_pct", "Investment return (%/yr)", 0, 15, p["invest_return_pct"], 0.5)
        self._slider(side, "savings_apy_pct", "Savings APY (%)", 0, 6, p["savings_apy_pct"], 0.25)
        self._slider(side, "horizon_weeks", "Horizon (weeks)", 26, 520, p["horizon_weeks"], 13)
        bf = ttk.Frame(side)
        bf.pack(fill="x", pady=10)
        ttk.Button(bf, text="Save as my defaults", command=self.save_defaults).pack(fill="x")
        ttk.Button(bf, text="Write plan to GridCraft", command=self.write_plan).pack(fill="x", pady=4)

        main = ttk.Frame(tab)
        main.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.sim_summary = ttk.Label(main, text="", justify="left", font=("TkDefaultFont", 11))
        self.sim_summary.pack(anchor="w")
        self.sim_fig = Figure(figsize=(8, 6), dpi=96)
        self.sim_canvas = FigureCanvasTkAgg(self.sim_fig, master=main)
        self.sim_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _slider(self, parent, key, label, lo, hi, value, step, target=None):
        target = self.vars if target is None else target
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=2)
        var = tk.DoubleVar(value=value)
        head = ttk.Frame(f)
        head.pack(fill="x")
        ttk.Label(head, text=label).pack(side="left")
        val_lbl = ttk.Label(head, text="", font=("TkDefaultFont", 10, "bold"))
        val_lbl.pack(side="left", padx=6)

        def show(*_):  # also runs when code sets the value
            v = var.get()
            val_lbl.config(text=f"{v:,.2f}".rstrip("0").rstrip(".") if step < 1 else f"{v:,.0f}")

        def on_move(_=None):
            var.set(round(var.get() / step) * step)
            self.schedule_sim()
        var.trace_add("write", show)
        ttk.Scale(f, from_=lo, to=hi, variable=var, command=on_move, length=310).pack(anchor="w")
        on_move()
        target[key] = (var, (lo, hi))
        return var

    def _rebuild_card_sliders(self):
        for w in self.card_frame.winfo_children():
            w.destroy()
        self.card_vars = {}
        for a in self.accounts:
            if str(a.get("Type", "")).lower() != "credit":
                continue
            bal = _num(a.get("Balance"))
            ttk.Label(self.card_frame, text=f"{a.get('Name')}  ({_num(a.get('APR %')):.2f}% APR, "
                                            f"min {money(_num(a.get('Min Payment')))}/mo)",
                      foreground="#444", wraplength=320).pack(anchor="w", pady=(4, 0))
            self._slider(self.card_frame, a.get("Name"), "   balance ($)", 0, max(bal * 2, 2000), bal, 10,
                         target=self.card_vars)

    def apply_income_defaults(self):
        wk, tax, _ = planner.weekly_income_from_records(self.income)
        if wk:  # for reference only: the plan uses the fixed weekly pay
            self.brb_ref.config(text=f"BRB average so far: {money(wk)}/wk gross")
        if tax and self.cfg["plan"]["tax_set_aside_pct"] == config.DEFAULTS["plan"]["tax_set_aside_pct"]:
            self.vars["tax_set_aside_pct"][0].set(round(tax * 2) / 2)

    def load_business(self):
        """Read the BRB planner's data (../brb) in the background; it runs pdftotext on its reports."""
        def work():
            try:
                s = business.snapshot(self.cfg["brb_owner_name"])
            except Exception as e:  # missing folder or unreadable files: show why, keep the app usable
                self.after(0, self._on_business, None, e)
            else:
                self.after(0, self._on_business, s, None)
        threading.Thread(target=work, daemon=True).start()

    def _on_business(self, s, err):
        self.biz = s
        if err:
            self.biz_lbl.config(text=f"Shop owner payout: unavailable ({err})")
            self.log(f"BRB planner data not loaded: {err}")
            return
        self.tiles["payout"].config(text=money(s["per_owner"]))
        text = (f"Shop owner payout estimate: {money(s['per_owner'])}/wk each (BRB planner, today's "
                f"prices, {money(s['growth'])}/wk growth money)")
        if s["me"]:
            text += f"\nYour barista pay {s['me']['year']}: {money(s['me']['avg_week'])}/wk average"
        if s["warnings"]:
            text += "\n⚠ " + "\n⚠ ".join(s["warnings"])
        self.biz_lbl.config(text=text)
        self.biz_use.state(["!disabled"])

    def use_payout(self):
        """Plan with the shop payout as the raise: pay stays as is until the raise week."""
        if self.biz:
            self.raise_on.set(True)
            self.vars["raise_to"][0].set(round(self.biz["per_owner"] / 5) * 5)
            self.schedule_sim()

    def open_brb(self):
        try:
            business.open_planner()
        except OSError as e:
            messagebox.showerror("BRB Planner", str(e))

    def _inputs(self):
        g = {k: v.get() for k, (v, _) in self.vars.items()}
        cards = []
        for a in self.accounts:
            if str(a.get("Type", "")).lower() == "credit":
                bal = self.card_vars[a["Name"]][0].get() if a.get("Name") in self.card_vars else _num(a.get("Balance"))
                cards.append(planner.Card(a.get("Name"), bal, _num(a.get("APR %")), _num(a.get("Min Payment"))))
        cash = sum(_num(a.get("Balance")) for a in self.accounts if str(a.get("Type", "")).lower()
                   in ("checking", "savings"))
        return planner.PlanInputs(
            weekly_gross=g["weekly_gross"], tax_set_aside_pct=g["tax_set_aside_pct"],
            income_change_pct=g["income_change_pct"], weekly_allowance=g["weekly_allowance"],
            weekly_bills=g["weekly_bills"], cards=cards, starting_cash=max(cash, 0),
            starter_buffer=g["starter_buffer"] if self.use_buffer.get() else 0,
            raise_to=g["raise_to"] if self.raise_on.get() else 0, raise_in_weeks=int(g["raise_in_weeks"]),
            strategy=self.strategy.get(), emergency_fund_target=g["emergency_fund_target"],
            invest_pct_after_debt=g["invest_pct_after_debt"], invest_return_pct=g["invest_return_pct"],
            savings_apy_pct=g["savings_apy_pct"], horizon_weeks=int(g["horizon_weeks"]))

    def schedule_sim(self):
        if self._sim_job:
            self.after_cancel(self._sim_job)
        self._sim_job = self.after(120, self.run_sim)

    def run_sim(self):
        self._sim_job = None
        if "horizon_weeks" not in self.vars:
            return
        p = self._inputs()
        res = planner.simulate(p)
        self.last_plan = (p, res)
        rows = res["rows"]

        def when(w):
            return f"week {w} ({(p.start + timedelta(weeks=w)).strftime('%b %d, %Y')})" if w else "–"
        if res["debt_free_week"] is None:
            debt_txt = "not within the horizon"
        elif res["debt_free_week"] == 0:
            debt_txt = "no card debt"
        else:
            debt_txt = when(res["debt_free_week"])
        nw = {y: rows[min(len(rows), 52 * y) - 1]["Net Worth"] for y in (1, 3) if rows}
        self.sim_summary.config(text=(
            f"Weekly: gross {money(res['weekly_gross'])}  →  after tax {money(res['weekly_net'])}  →  "
            f"after allowance & bills {money(res['weekly_spare'])}\n"
            f"Cards paid off: {debt_txt}   ·   interest paid: {money(res['total_interest'])}\n"
            f"Emergency fund full: {when(res['fund_full_week']) if res['fund_full_week'] else '–'}   ·   "
            f"tax set aside in first year: {money(res['tax_saved'])}\n"
            f"Net worth in 1 yr: {money(nw.get(1, 0))}   ·   3 yrs: {money(nw.get(3, 0))}   ·   "
            f"end ({len(rows)} wks): {money(rows[-1]['Net Worth']) if rows else '–'}"))
        self.tiles["debtfree"].config(text=debt_txt if res["debt_free_week"] != 0 else "Done")
        self.week_plan.config(state="normal")
        self.week_plan.delete("1.0", "end")
        self.week_plan.insert("end", "\n".join(planner.this_week_breakdown(p, res)))
        self.week_plan.config(state="disabled")

        self.sim_fig.clear()
        weeks = [r["Week"] for r in rows]
        ax1 = self.sim_fig.add_subplot(211)
        names = list(rows[0]["per_card"]) if rows else []
        if names and any(c.balance for c in p.cards):
            ax1.stackplot(weeks, *[[r["per_card"][n] for r in rows] for n in names], labels=names, alpha=0.85)
            ax1.legend(loc="upper right", fontsize=8, frameon=False)
        else:
            ax1.text(0.5, 0.5, "No card balances (add them on the Accounts tab)", ha="center",
                     transform=ax1.transAxes, color="#777")
        ax1.set_title("Credit card balances", loc="left", fontsize=10)
        ax2 = self.sim_fig.add_subplot(212, sharex=ax1)
        ax2.plot(weeks, [r["Emergency Fund"] for r in rows], label="Savings / emergency fund", color="#2a78b8")
        ax2.plot(weeks, [r["Invested"] for r in rows], label="Invested", color="#1f9d55")
        ax2.plot(weeks, [r["Net Worth"] for r in rows], label="Net worth", color="#222", linestyle="--")
        ax2.axhline(0, color="#bbb", linewidth=0.8)
        ax2.legend(loc="upper left", fontsize=8, frameon=False)
        ax2.set_title("Savings, investments and net worth", loc="left", fontsize=10)
        ax2.set_xlabel("Week")
        for ax in (ax1, ax2):
            ax.spines[["top", "right"]].set_visible(False)
            ax.yaxis.set_major_formatter(lambda v, _: f"${v:,.0f}")
        self.sim_fig.tight_layout()
        self.sim_canvas.draw_idle()

    def save_defaults(self):
        for k, (v, _) in self.vars.items():
            if k in self.cfg["plan"]:
                self.cfg["plan"][k] = v.get()
        self.cfg["plan"]["fixed_weekly_pay"] = self.vars["weekly_gross"][0].get()
        self.cfg["plan"]["strategy"] = self.strategy.get()
        config.save(self.cfg)
        self.log("Saved simulator defaults to config.json")

    def write_plan(self):
        if not getattr(self, "last_plan", None):
            return
        _, res = self.last_plan
        if self.gc_guard(self.book.replace, "Plan", res["rows"]):
            self.gc_guard(self.book.format_dates, "Plan")
            self.log(f"Wrote {len(res['rows'])} weeks to the Plan sheet")
            messagebox.showinfo("Plan", f"Wrote {len(res['rows'])} weeks to the Plan sheet in GridCraft.")

    # =============================================================== advisor
    def _build_advisor(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="Advisor")
        self.advisor = advisor.Advisor(self.cfg["advisor_model"], self.cfg["advisor_max_tokens"])
        self._adv_queue = queue.Queue()

        keyrow = ttk.Frame(tab)
        keyrow.pack(fill="x")
        self.adv_key_lbl = ttk.Label(keyrow, text="")
        self.adv_key_lbl.pack(side="left")
        ttk.Button(keyrow, text="Set API key…", command=self.set_api_key).pack(side="right")
        ttk.Label(tab, text=f"Uses {self.cfg['advisor_model']}. Sends a summary (balances, category totals, "
                            "merchant names, plan settings) to Anthropic's API, not your statement files.",
                  foreground="#555", wraplength=1100).pack(anchor="w", pady=(2, 8))

        quick = ttk.Frame(tab)
        quick.pack(fill="x")
        self.adv_buttons = []
        for label, prompt in advisor.PROMPTS.items():
            b = ttk.Button(quick, text=label, command=lambda q=prompt, l=label: self.ask_advisor(q, l))
            b.pack(side="left", padx=(0, 6))
            self.adv_buttons.append(b)

        ask = ttk.Frame(tab)
        ask.pack(fill="x", pady=8)
        self.adv_entry = ttk.Entry(ask)
        self.adv_entry.pack(side="left", fill="x", expand=True)
        self.adv_entry.bind("<Return>", lambda e: self._ask_typed())
        b = ttk.Button(ask, text="Ask", command=self._ask_typed)
        b.pack(side="left", padx=6)
        self.adv_buttons.append(b)
        ttk.Button(ask, text="New conversation", command=self.reset_advisor).pack(side="left")
        ttk.Button(ask, text="Save advice", command=self.save_advice).pack(side="left", padx=6)

        self.adv_text = tk.Text(tab, wrap="word", font=("TkDefaultFont", 11), padx=10, pady=8)
        self.adv_text.tag_configure("you", foreground="#2a78b8", font=("TkDefaultFont", 11, "bold"))
        self.adv_text.pack(fill="both", expand=True)
        self.adv_status = ttk.Label(tab, text="", foreground="#555")
        self.adv_status.pack(anchor="w", pady=(4, 0))
        self._refresh_key_label()

    def _refresh_key_label(self):
        has_key = bool(advisor.load_key())
        self.adv_key_lbl.config(text="API key: set ✓" if has_key else "API key: not set. Add one to use the advisor.",
                                foreground=GREEN if has_key else RED)

    def set_api_key(self):
        win = tk.Toplevel(self)
        win.title("Anthropic API key")
        win.transient(self)
        ttk.Label(win, text="Paste your Anthropic API key (console.anthropic.com → API keys).\n"
                            f"It's saved only on this computer, in {advisor.KEY_FILE}.",
                  padding=10).pack()
        var = tk.StringVar()
        ttk.Entry(win, textvariable=var, show="•", width=60).pack(padx=10)

        def ok():
            if var.get().strip():
                advisor.save_key(var.get())
            win.destroy()
            self._refresh_key_label()
        ttk.Button(win, text="Save", command=ok).pack(pady=10)

    def _ask_typed(self):
        q = self.adv_entry.get().strip()
        if q:
            self.adv_entry.delete(0, "end")
            self.ask_advisor(q, q)

    def ask_advisor(self, question, label):
        if not advisor.load_key():
            messagebox.showinfo("Advisor", "Set your Anthropic API key first (Set API key…).")
            return
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            fix = ("sudo apt install python3-pip\n  pip install --user --break-system-packages jiter"
                   if getattr(e, "name", "") == "jiter" else "sudo apt install python3-anthropic")
            messagebox.showerror("Advisor", f"The Anthropic SDK can't load ({e}). Fix with:\n  {fix}")
            return
        if not getattr(self, "last_plan", None):
            self.run_sim()
        p, res = self.last_plan
        summary = advisor.build_summary(self.income, self.transactions, self.accounts, p, res,
                                        business=business.describe(self.biz) if self.biz else None)
        for b in self.adv_buttons:
            b.state(["disabled"])
        self.adv_text.insert("end", f"\n› {label}\n\n", "you")
        self.adv_text.see("end")
        self.adv_status.config(text="Thinking…")
        self._adv_pending = (question, label)

        def work():
            try:
                text, cost = self.advisor.ask(question, summary, lambda t: self._adv_queue.put(("text", t)))
                self._adv_queue.put(("done", (text, cost)))
            except Exception as e:  # network, auth, refusal: show it in the tab
                self._adv_queue.put(("error", e))
        threading.Thread(target=work, daemon=True).start()
        self.after(50, self._drain_advisor)

    def _drain_advisor(self):
        try:
            while True:
                kind, val = self._adv_queue.get_nowait()
                if kind == "text":
                    self.adv_text.insert("end", val)
                    self.adv_text.see("end")
                    continue
                for b in self.adv_buttons:
                    b.state(["!disabled"])
                if kind == "done":
                    text, cost = val
                    self.adv_text.insert("end", "\n")
                    saved = self.record_advice(text, cost)
                    self.adv_status.config(text=f"This answer cost about ${cost:.4f} · "
                                                f"conversation so far ${self.advisor.spent:.4f}"
                                                + (" · saved to the Advice sheet" if saved else ""))
                else:
                    self.adv_text.insert("end", f"\n[Error: {val}]\n")
                    self.adv_status.config(text="")
                return
        except queue.Empty:
            self.after(50, self._drain_advisor)

    def record_advice(self, text, cost):
        """Append the answer to the Advice sheet so it's there for the year-end review."""
        question, label = self._adv_pending
        if not text.strip():  # nothing worth keeping (e.g. the answer hit the length limit while thinking)
            return False
        today = date.today()
        first = today.replace(day=1)
        if label.startswith("Prep"):
            month = (first + timedelta(days=32)).replace(day=1)
        elif label.startswith("Review"):
            month = (first - timedelta(days=1)).replace(day=1)
        else:
            month = first
        stamp = datetime.now().isoformat(timespec="seconds")
        rec = {"Date": today, "For Month": f"{month:%Y-%m}", "Topic": label if label in advisor.PROMPTS
               else "Question", "Question": question, "Advice": text.strip(), "Model": self.advisor.model,
               "Cost $": round(cost, 4), "Key": stamp}
        if self.gc_guard(self.book.append, "Advice", [rec]) is None:
            return False
        self.gc_guard(self.book.format_dates, "Advice")
        self.gc_guard(self.book.wrap_column, "Advice", "Advice", 100)
        self.gc_guard(self.book.wrap_column, "Advice", "Question", 40)
        self.log(f"Saved advisor answer ({rec['Topic']}, for {rec['For Month']}) to the Advice sheet")
        return True

    def reset_advisor(self):
        self.advisor.reset()
        self.adv_text.delete("1.0", "end")
        self.adv_status.config(text="")

    def save_advice(self):
        text = self.adv_text.get("1.0", "end").strip()
        if not text:
            return
        folder = config.PROJECT_DIR / "advice"
        folder.mkdir(exist_ok=True)
        path = folder / f"{date.today()}_advice.txt"
        path.write_text(text + "\n")
        self.log(f"Saved advice to {path.relative_to(config.PROJECT_DIR)}")
        messagebox.showinfo("Advisor", f"Saved to {path}")


def main():
    App().mainloop()
