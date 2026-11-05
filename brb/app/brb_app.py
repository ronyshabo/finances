"""BRB owner payout planner — desktop app (Tkinter).

Run:  python3 app/brb_app.py            (reads the data/ folder next to app/)
      python3 app/brb_app.py <folder>
"""
import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brb_data as bd  # noqa: E402

FOLDER = sys.argv[1] if len(sys.argv) > 1 else bd.DEFAULT_FOLDER
os.makedirs(FOLDER, exist_ok=True)

BG = "#f5f6f8"
CARD = "#ffffff"
INK = "#1f2933"
MUTED = "#616e7c"
GOOD = "#2e7d32"
BAD = "#c62828"
ACCENT = "#1f3864"
SEGMENTS = [  # key, label, color
    ("cogs", "Cost of goods", "#8d6e63"),
    ("fees", "Card fees", "#b0bec5"),
    ("bills", "Rent & bills", "#ffb74d"),
    ("staff", "Staff base pay", "#64b5f6"),
    ("cover", "Covering your shifts", "#9575cd"),
    ("growth", "Growth money", "#4db6ac"),
    ("owners", "Owners (split 4 ways)", "#2e7d32"),
]
GROWTH_MAX, GROWTH_STEP = 1500, 25
PRICE_MAX = 30.0


def money(v, cents=False):
    s = f"${abs(v):,.2f}" if cents else f"${abs(v):,.0f}"
    return "-" + s if v < 0 else s


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("BRB Owner Payout Planner")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1360, sw - 20)}x{min(860, sh - 90)}+0+0")
        self.minsize(1000, 600)
        try:
            self.attributes("-zoomed", True)
        except tk.TclError:
            pass
        self.configure(bg=BG)
        self._style()

        self.growth = tk.DoubleVar(value=200)
        self.price = tk.DoubleVar(value=5.0)
        self.loss = tk.DoubleVar(value=2.0)
        self.fee = tk.DoubleVar(value=3.0)
        self.rate = tk.DoubleVar(value=0.0)
        self.goal = tk.DoubleVar(value=500)
        self.basis = tk.StringVar(value="Average of full weeks")
        self.transfers = tk.BooleanVar(value=True)
        self.result = None

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=6)
        self.results_tab = ttk.Frame(nb, style="Bg.TFrame")
        self.files_tab = ttk.Frame(nb, style="Bg.TFrame")
        nb.add(self.results_tab, text="  Results  ")
        nb.add(self.files_tab, text="  Files  ")
        self._build_results()
        self._build_files()
        self.reload(first=True)

    # ------------------------------------------------------------ styling
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("Bg.TFrame", background=BG)
        s.configure("Card.TFrame", background=CARD, relief="flat")
        s.configure("TLabel", background=BG, foreground=INK, font=("DejaVu Sans", 10))
        s.configure("Card.TLabel", background=CARD, foreground=INK)
        s.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("DejaVu Sans", 9))
        s.configure("H.TLabel", background=BG, foreground=INK, font=("DejaVu Sans", 13, "bold"))
        s.configure("Big.TLabel", background=CARD, foreground=INK, font=("DejaVu Sans", 17, "bold"))
        s.configure("Mid.TLabel", background=CARD, foreground=INK, font=("DejaVu Sans", 12, "bold"))
        s.configure("Card.TCheckbutton", background=CARD)
        s.configure("TNotebook.Tab", font=("DejaVu Sans", 11, "bold"), padding=(12, 6))
        s.configure("Treeview", rowheight=24, font=("DejaVu Sans", 9))
        s.configure("Treeview.Heading", font=("DejaVu Sans", 9, "bold"))

    def card(self, parent, **grid):
        f = ttk.Frame(parent, style="Card.TFrame", padding=(10, 6))
        f.grid(**grid, sticky="nsew", padx=4, pady=3)
        f.bind("<Configure>", lambda e: [w.configure(wraplength=max(e.width - 22, 80))
                                         for w in f.winfo_children() if getattr(w, "wrap_auto", False)])
        return f

    # ------------------------------------------------------------ results tab
    def _build_results(self):
        t = self.results_tab
        t.columnconfigure(1, weight=1)
        t.rowconfigure(0, weight=1)

        left = ttk.Frame(t, style="Bg.TFrame")
        left.grid(row=0, column=0, sticky="ns", padx=(0, 6))
        right = ttk.Frame(t, style="Bg.TFrame")
        right.grid(row=0, column=1, sticky="nsew")

        # --- sliders
        c = self.card(left, row=0, column=0)
        ttk.Label(c, text="Growth money kept in the business", style="Muted.TLabel").pack(anchor="w")
        self.growth_lbl = ttk.Label(c, style="Big.TLabel")
        self.growth_lbl.pack(anchor="w")
        self.growth_sub = ttk.Label(c, style="Muted.TLabel")
        self.growth_sub.pack(anchor="w")
        ttk.Scale(c, from_=0, to=GROWTH_MAX, variable=self.growth, length=300,
                  command=lambda v: self._snap(self.growth, v, GROWTH_STEP)).pack(fill="x", pady=(4, 0))
        ttk.Label(c, text=f"$0 per week  ←  slide  →  {money(GROWTH_MAX)} per week",
                  style="Muted.TLabel").pack(anchor="w")

        c = self.card(left, row=1, column=0)
        ttk.Label(c, text="Price increase", style="Muted.TLabel").pack(anchor="w")
        self.price_lbl = ttk.Label(c, style="Big.TLabel")
        self.price_lbl.pack(anchor="w")
        ttk.Scale(c, from_=0, to=PRICE_MAX, variable=self.price, length=300,
                  command=lambda v: self._snap(self.price, v, 0.5)).pack(fill="x", pady=(4, 0))
        ttk.Label(c, text=f"0%  ←  slide  →  {PRICE_MAX:.0f}%", style="Muted.TLabel").pack(anchor="w")

        # --- assumptions
        c = self.card(left, row=2, column=0)
        ttk.Label(c, text="Assumptions", style="Mid.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        rows = [
            ("Sales to plan with", ttk.Combobox(c, textvariable=self.basis, state="readonly", width=20,
                                                values=["Average of full weeks", "Last 4 full weeks", "Lowest week"])),
            ("Customers lost from price increase (%)", ttk.Spinbox(c, from_=0, to=30, increment=0.5,
                                                                   textvariable=self.loss, width=7)),
            ("Card processing fees (%)", ttk.Spinbox(c, from_=0, to=10, increment=0.1,
                                                     textvariable=self.fee, width=7)),
            ("Barista pay for your shifts ($/hr)", ttk.Spinbox(c, from_=0, to=40, increment=0.25,
                                                              textvariable=self.rate, width=7)),
            ("Pay goal per owner ($/week)", ttk.Spinbox(c, from_=0, to=2000, increment=25,
                                                       textvariable=self.goal, width=7)),
        ]
        for i, (lab, w) in enumerate(rows, start=1):
            ttk.Label(c, text=lab, style="Card.TLabel", wraplength=190).grid(row=i, column=0, sticky="w", pady=1)
            w.grid(row=i, column=1, sticky="e", padx=(6, 0))
        ttk.Checkbutton(c, text="Count the transfers to account …3354\nas cost of goods",
                        variable=self.transfers, style="Card.TCheckbutton",
                        command=self.update_view).grid(row=len(rows) + 1, column=0, columnspan=2, sticky="w", pady=4)
        for var in (self.loss, self.fee, self.rate, self.goal, self.basis):
            var.trace_add("write", lambda *_: self.update_view())
        for var in (self.growth, self.price):
            var.trace_add("write", lambda *_: self.update_view())

        c = self.card(left, row=3, column=0)
        ttk.Label(c, text="Data notes", style="Mid.TLabel").pack(anchor="w")
        self.notes = ttk.Label(c, style="Muted.TLabel", justify="left")
        self.notes.wrap_auto = True
        self.notes.pack(anchor="w")
        left.rowconfigure(3, weight=1)

        # --- owner tiles
        right.columnconfigure(tuple(range(4)), weight=1, uniform="o")
        self.owner_lbls = []
        for i, name in enumerate(bd.ALL_OWNERS):
            c = self.card(right, row=0, column=i)
            ttk.Label(c, text=name, style="Muted.TLabel").pack(anchor="w")
            wk = ttk.Label(c, style="Big.TLabel")
            wk.pack(anchor="w")
            mo = ttk.Label(c, style="Muted.TLabel")
            mo.pack(anchor="w")
            gl = ttk.Label(c, style="Muted.TLabel")
            gl.pack(anchor="w")
            self.owner_lbls.append((wk, mo, gl))

        # --- info tiles
        self.info_lbls = []
        for i, lab in enumerate(["Weekly sales after price change", "Left for owners + growth, per week",
                                 "Growth money per year", "Weekly sales needed so every owner gets the goal"]):
            c = self.card(right, row=1, column=i)
            hl = ttk.Label(c, text=lab, style="Muted.TLabel", justify="left")
            hl.wrap_auto = True
            hl.pack(anchor="w")
            v = ttk.Label(c, style="Mid.TLabel")
            v.pack(anchor="w")
            s = ttk.Label(c, style="Muted.TLabel", justify="left")
            s.wrap_auto = True
            s.pack(anchor="w")
            self.info_lbls.append((v, s))

        # --- charts
        c = self.card(right, row=2, column=0, columnspan=4)
        ttk.Label(c, text="Where each week's sales go", style="Mid.TLabel").pack(anchor="w")
        self.bar = tk.Canvas(c, height=86, bg=CARD, highlightthickness=0)
        self.bar.pack(fill="x", expand=True)
        self.bar.bind("<Configure>", lambda e: self.draw_bar())

        c = self.card(right, row=3, column=0, columnspan=4)
        ttk.Label(c, text="Growth money vs. payout per owner (per week)", style="Mid.TLabel").pack(anchor="w")
        self.line = tk.Canvas(c, height=170, bg=CARD, highlightthickness=0)
        self.line.pack(fill="both", expand=True)
        self.line.bind("<Configure>", lambda e: self.draw_line())
        right.rowconfigure(3, weight=1)

        self.footer = ttk.Label(right, style="TLabel", foreground=MUTED, justify="left", font=("DejaVu Sans", 9))
        right.bind("<Configure>", lambda e: self.footer.configure(wraplength=max(e.width - 20, 200)))
        self.footer.grid(row=4, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 0))

    def _snap(self, var, value, step):
        v = round(float(value) / step) * step
        if abs(var.get() - v) > 1e-9:
            var.set(v)

    def _num(self, var, default):
        try:
            return float(var.get())
        except (tk.TclError, ValueError):
            return default

    def params(self):
        return dict(price=self._num(self.price, 0) / 100, loss=self._num(self.loss, 0) / 100,
                    fee=self._num(self.fee, 0) / 100, rate=self._num(self.rate, 0),
                    include_transfers=self.transfers.get(), basis=self.basis.get())

    def update_view(self):
        if not hasattr(self, "data"):
            return
        g = self._num(self.growth, 0)
        p = self.params()
        r = self.result = bd.compute(self.data, g, **p)
        goal = self._num(self.goal, 0)

        self.growth_lbl.config(text=f"{money(g)} / week")
        self.growth_sub.config(text=f"{money(g * 52 / 12)} per month · {money(g * 52)} per year")
        self.price_lbl.config(text=f"{p['price']:.1%}")

        per = r["per_owner"]
        color = GOOD if per >= goal else (BAD if r["pool"] < 0 else INK)
        for wk, mo, gl in self.owner_lbls:
            wk.config(text=f"{money(per)} / wk", foreground=color)
            mo.config(text=f"{money(per * 52 / 12)} per month")
            gl.config(text=f"{money(goal - per)}/wk below the goal" if per < goal else "goal met",
                      foreground=GOOD if per >= goal else MUTED)

        (v0, s0), (v1, s1), (v2, s2), (v3, s3) = self.info_lbls
        v0.config(text=money(r["sales"]))
        s0.config(text=f"today: {money(r['base_sales'])} ({r['weeks']} full weeks, {self.basis.get().lower()})")
        v1.config(text=money(r["before_owners"]), foreground=GOOD if r["before_owners"] >= 0 else BAD)
        s1.config(text="after cost of goods, fees, bills and all barista pay")
        v2.config(text=money(g * 52))
        s2.config(text="kept for equipment, repairs, a slow month")
        need = self.sales_needed(r, p, g, goal)
        v3.config(text=money(need) if need is not None else "n/a",
                  foreground=GOOD if need is not None and r["sales"] >= need else BAD)
        s3.config(text=(f"{money(need - r['sales'])} more per week than now ({money((need - r['sales']) / 7)}/day)"
                        if need is not None and need > r["sales"] else "you're there at these settings")
                  if need is not None else "price too low to cover cost of goods")

        notes = list(self.data.warnings)
        notes.append(f"Cost of goods: {r['cogs_pct']:.1%} of sales ({r['cogs_label']}).")
        notes.append(f"Monthly bills from files: {money(r['bills_m'])}.")
        if self.data.cash:
            notes.append(f"Cash in bank: {money(self.data.cash[1])} as of {self.data.cash[0]:%b %d, %Y}.")
        self.notes.config(text="\n".join("• " + n for n in notes))
        self.footer.config(text=(
            f"Assumes Rony & Youssef have stepped out of shifts: baristas cover {r['owner_hours']:.0f} hrs/week at "
            f"{money(r['rate'], True)}/hr. Whatever is left after all costs and growth money is split equally "
            f"between {', '.join(bd.ALL_OWNERS)}. Price increase raises sales; cost of goods stays the same per "
            f"customer, so it falls as a share of sales."))
        self.draw_bar()
        self.draw_line()

    def sales_needed(self, r, p, g, goal):
        """Today's-price weekly sales needed so the 4-way split reaches the goal (shown after price change)."""
        fixed = r["costs"]["bills"] + r["costs"]["staff"] + r["costs"]["cover"] + g + goal * r["owners"]
        margin = (1 - p["loss"]) * ((1 + p["price"]) * (1 - p["fee"]) - r["cogs_pct"])
        if margin <= 0:
            return None
        return fixed / margin * (1 + p["price"]) * (1 - p["loss"])

    # ------------------------------------------------------------ charts
    def draw_bar(self):
        cv, r = self.bar, self.result
        cv.delete("all")
        if not r:
            return
        w = max(cv.winfo_width(), 200)
        x0, x1, y0, h = 10, w - 10, 6, 30
        parts = dict(r["costs"], growth=r["growth"], owners=max(0.0, r["pool"]))
        total = max(sum(parts.values()), r["sales"], 1)
        x = x0
        for key, label, color in SEGMENTS:
            seg = parts[key] / total * (x1 - x0)
            if seg > 0:
                cv.create_rectangle(x, y0, x + seg, y0 + h, fill=color, outline=CARD, width=1)
                if seg > 60:
                    cv.create_text(x + seg / 2, y0 + h / 2, text=money(parts[key]), fill="white",
                                   font=("DejaVu Sans", 9, "bold"))
            x += seg
        sales_x = x0 + r["sales"] / total * (x1 - x0)
        if r["pool"] < 0:
            cv.create_rectangle(sales_x, y0, x1, y0 + h, fill="", outline=BAD, width=2, dash=(4, 2))
        lx, ly = x0, y0 + h + 10
        for key, label, color in SEGMENTS:
            short = key == "owners" and r["pool"] < 0
            cv.create_rectangle(lx, ly, lx + 12, ly + 12, fill=color, outline="")
            t = cv.create_text(lx + 16, ly + 6, anchor="w", text=f"{label} {money(parts[key])}" +
                               (f" — short {money(-r['pool'])}/wk (red box)" if short else ""),
                               font=("DejaVu Sans", 9, "bold" if short else "normal"), fill=BAD if short else INK)
            lx = cv.bbox(t)[2] + 16
            if lx > x1 - 220:
                lx, ly = x0, ly + 20

    def draw_line(self):
        cv, r = self.line, self.result
        cv.delete("all")
        if not r:
            return
        w, h = max(cv.winfo_width(), 300), max(cv.winfo_height(), 160)
        L, R, T, B = 70, w - 20, 15, h - 40
        p = self.params()
        pts = [(g, bd.compute(self.data, g, **p)["per_owner"]) for g in range(0, GROWTH_MAX + 1, GROWTH_STEP)]
        goal = self._num(self.goal, 0)
        ymax = max(max(v for _, v in pts), goal, 100) * 1.15
        sx = lambda g: L + g / GROWTH_MAX * (R - L)
        sy = lambda v: B - v / ymax * (B - T)
        step = next(s for s in (25, 50, 100, 200, 250, 500, 1000, 2000) if ymax / s <= 6)
        v = 0
        while v <= ymax:
            cv.create_line(L, sy(v), R, sy(v), fill="#e4e7eb")
            cv.create_text(L - 8, sy(v), text=money(v), anchor="e", font=("DejaVu Sans", 8), fill=MUTED)
            v += step
        for g in range(0, GROWTH_MAX + 1, 250):
            cv.create_text(sx(g), B + 12, text=money(g), font=("DejaVu Sans", 8), fill=MUTED)
        cv.create_text((L + R) / 2, B + 28, text="Growth money kept per week", font=("DejaVu Sans", 9), fill=MUTED)
        cv.create_line(L, B, R, B, fill=MUTED)
        if goal > 0:
            cv.create_line(L, sy(goal), R, sy(goal), fill=ACCENT, dash=(6, 3))
            cv.create_text(R, sy(goal) - 8, text=f"goal {money(goal)}/wk", anchor="e",
                           font=("DejaVu Sans", 8, "bold"), fill=ACCENT)
        cv.create_line(*[c for g, v in pts for c in (sx(g), sy(v))], fill=GOOD, width=3, smooth=False)
        g = r["growth"]
        cx, cy = sx(g), sy(r["per_owner"])
        cv.create_line(cx, T, cx, B, fill="#9aa5b1", dash=(2, 3))
        cv.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill=GOOD, outline="white", width=2)
        anchor = "sw" if cx < R - 200 else "se"
        cv.create_text(cx + (8 if anchor == "sw" else -8), cy - 8, anchor=anchor, fill=INK,
                       font=("DejaVu Sans", 9, "bold"),
                       text=f"keep {money(g)} → {money(r['per_owner'])} each")

    # ------------------------------------------------------------ files tab
    def _build_files(self):
        t = self.files_tab
        bar = ttk.Frame(t, style="Bg.TFrame")
        bar.pack(fill="x", pady=(4, 8))
        ttk.Button(bar, text="Add files…", command=self.add_files).pack(side="left", padx=4)
        ttk.Button(bar, text="Make bills template", command=self.make_bills).pack(side="left", padx=4)
        ttk.Button(bar, text="Open data folder", command=lambda: self.open_path(FOLDER)).pack(side="left", padx=4)
        ttk.Button(bar, text="Reload", command=self.reload).pack(side="left", padx=4)
        ttk.Label(bar, text=f"Data folder: {FOLDER}", foreground=MUTED).pack(side="left", padx=12)

        cols = ("kind", "covers", "summary", "status")
        self.tree = ttk.Treeview(t, columns=cols, show="tree headings")
        self.tree.heading("#0", text="File")
        for col, lab, wid in zip(cols, ("Type", "Covers", "What's in it", "Status"), (100, 190, 380, 200)):
            self.tree.heading(col, text=lab)
            self.tree.column(col, width=wid, anchor="w")
        self.tree.column("#0", width=430, minwidth=300)
        self.tree.tag_configure("bad", foreground=BAD)
        self.tree.tag_configure("muted", foreground=MUTED)
        self.tree.pack(fill="both", expand=True, padx=4)
        self.tree.bind("<Double-1>", lambda e: self.open_selected())

        help_text = (
            "Add several files at once — each is recognized by what's inside it and copied into the data folder:\n"
            "  • Sales: the POS daily export CSV (Day, Gross Sales, … Net Sales …). One file per week is fine.\n"
            "  • Payroll: the barista payout tax summary CSV (Name, Year, Weeks Paid, …). The newest one is used.\n"
            "  • Bills: a CSV with columns Bill, Monthly Amount. Click 'Make bills template' to start one; "
            "you can add more than one bills file.\n"
            "  • Cost of goods: the accounting 'Transactions by Account' PDF for Cost of Goods Sold.\n"
            "  • Balance sheet: the accounting 'Balance Sheet' PDF (for cash in the bank).\n"
            "Double-click a file to open it.")
        ttk.Label(t, text=help_text, justify="left", foreground=MUTED).pack(anchor="w", padx=6, pady=8)

    def fill_files(self):
        self.tree.delete(*self.tree.get_children())
        order = {"sales": 0, "payroll": 1, "bills": 2, "cogs": 3, "balance": 4, "unknown": 5}
        names = {"sales": "Sales", "payroll": "Payroll", "bills": "Bills", "cogs": "Cost of goods",
                 "balance": "Balance sheet", "unknown": "—"}
        for f in sorted(self.data.files, key=lambda f: (order[f.kind], os.path.basename(f.path))):
            tag = "bad" if f.status not in ("OK",) and f.kind != "unknown" else ("muted" if f.kind == "unknown" else "")
            if f.status.startswith("Older"):
                tag = "muted"
            self.tree.insert("", "end", iid=f.path, text=os.path.basename(f.path), tags=(tag,),
                             values=(names[f.kind], f.covers, f.summary, f.status))

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Add files", filetypes=[("Sales, payroll, bills, reports", "*.csv *.pdf"), ("All files", "*")])
        if not paths:
            return
        copied, skipped = bd.import_files(
            paths, FOLDER, lambda n: messagebox.askyesno("Replace file?", f"{n} is already in the data folder.\n"
                                                                          "Replace it with the new one?"))
        msg = []
        if copied:
            msg.append("Added:\n  " + "\n  ".join(copied))
        if skipped:
            msg.append("Skipped:\n  " + "\n  ".join(f"{n} — {why}" for n, why in skipped))
        messagebox.showinfo("Add files", "\n\n".join(msg) or "Nothing to add.")
        self.reload()

    def make_bills(self):
        path, created = bd.write_bills_template(FOLDER)
        if created:
            messagebox.showinfo("Bills template", f"Created {os.path.basename(path)} with rent at $2,700.\n"
                                                  "Fill in the other bills, save, then click Reload.")
        else:
            messagebox.showinfo("Bills template", f"{os.path.basename(path)} already exists — opening it.")
        self.open_path(path)
        self.reload()

    def open_selected(self):
        sel = self.tree.selection()
        if sel:
            self.open_path(sel[0])

    @staticmethod
    def open_path(path):
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def reload(self, first=False):
        self.data = bd.load(FOLDER)
        if first or self._num(self.rate, 0) == 0:
            if self.data.payroll:
                self.rate.set(round(self.data.payroll["staff_rate"], 2))
        self.fill_files()
        self.update_view()


if __name__ == "__main__":
    App().mainloop()
