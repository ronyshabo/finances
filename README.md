# Finances

- `brb/` — BRB coffee shop owner payout planner (Tkinter desktop app). Run `python3 brb/app/brb_app.py`; it reads and saves files in `brb/data/`.
- `personal/` — personal finance app (Tkinter): imports bank, card and barista earnings statements into a
  GridCraft workbook, plans card payoff / savings / investing, and has a Claude advisor tab. Run
  `python3 personal/main.py`; see `personal/README.md`.

The personal app reads `brb/data/` (read-only, through `brb/app/brb_data.py`) to show the owner payout
estimate and to plan with it. It never writes to `brb/`. Personal statements, settings and logs stay
on the owner's computer: `personal/.gitignore` keeps them out of git.
