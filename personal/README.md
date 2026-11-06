# Personal Finance

A local Tkinter app that imports BRB earnings statements and bank / credit card
statements into a GridCraft master workbook, then plans card payoff, savings and
investing with live what-if sliders.

## Setup

```sh
sudo apt install python3-tk python3-matplotlib python3-anthropic poppler-utils
python3 personal/main.py
```

## What stays private

The repo is public. `personal/.gitignore` keeps everything personal on this computer:
`inbox/` contents, `archive/` (imported statements), `advice/`, `logs/` and `config.json`
(card numbers and your saved plan defaults). The financial data itself lives in the GridCraft
workbook under `~/Documents`, outside the repo. Check `git status` before committing; none of
those should ever show up.

## Desktop shortcut

`run.sh` starts the app (output goes to `logs/app.log`). Shortcuts are per-user files outside the
repo: `~/.local/share/applications/personal-finance.desktop` (app menu) and
`~/Desktop/personal-finance.desktop`, with `Exec` pointing at `personal/run.sh`. On Ubuntu,
right-click the Desktop icon once and choose **Allow Launching**.

## BRB owner payout (../brb)

The Plan tab and the Dashboard show the shop's **owner payout estimate** from the BRB planner,
calculated by `brb/app/brb_data.py` on `brb/data/` at today's prices with $200/week growth money,
plus your own barista pay from the payroll summary (`brb_owner_name` in `config.json`).
**Use as the raise** plans with that payout as your future weekly pay; **Open BRB Planner** opens
the full planner. The Advisor gets the same numbers. This is read-only: nothing in `brb/` is changed.
If the BRB data has no bills file, rent isn't counted and the estimate is too high; the app says so.

## Adding statements

Drop files into `inbox/` (or use **Add files…**). On launch, or with **Process inbox**,
each file is identified by its name, parsed, written to GridCraft, and moved to
`archive/<kind>/` with a standard name such as `2026-08_card_Discover-it-1234.pdf`.

| File name | Identified as |
|---|---|
| `20260801-Bank statement.pdf` | Capital One 360 bank statement, Aug 2026 (all sub-accounts) |
| `Statement_082026_1234.pdf` | card ending 1234 (Capital One or Discover, named in `config.json`), Aug 2026 |
| anything with `brb` / `earnings` | BRB earnings statement (.txt or .pdf) |

Patterns live in `config.json` under `filename_patterns`, and card names under `cards`
(last four digits → name, e.g. `{"cards": {"1234": "Discover it 1234"}}`). A new card only needs a line there. If the name and the
contents disagree, the contents win and the log says so.

## Advisor tab (Claude API)

Asks Claude (`claude-haiku-5-5`, the smallest current model) for month-ahead prep, a review of last
month, or any question you type. It sends a summary built by `finance/advisor.py` (balances, monthly
category totals, recurring charges, top merchants, plan settings), not the statement files.
Answers are capped at 16,000 tokens (thinking included), so each costs under a cent; the tab shows the cost
after every answer. The model and cap are `advisor_model` / `advisor_max_tokens` in `config.json`.

The API key comes from `ANTHROPIC_API_KEY`, or from **Set API key…**, which saves it to
`~/.config/personal-finance/anthropic_key` (readable only by you).

## How it connects to GridCraft

GridCraft is installed as a Flatpak with no network access and only
`~/Documents` and `~/Downloads` visible, so:

- The master workbook lives at `~/Documents/PersonalFinance/Finance_Master.xlsx`.
- On launch the app connects to GridCraft's control channel on port 7979. If
  nothing is listening it starts GridCraft itself:
  `flatpak run --share=network --command=gridcraft ai.storyteller.gridcraft --control 7979 <master>`.
  A GridCraft window opened from the desktop icon has no control port, so let the
  app open it.
- GridCraft only answers the app while its window is on screen. **Don't minimize it**; if the
  app reports a GridCraft error, restore the window (close any dialog in it) and press **Reconnect**.

Port, master path and planner defaults are in `config.json` (created by
**Save as my defaults**).

## Workbook sheets

| Sheet | Filled by |
|---|---|
| Overview | marker the app uses to recognise the master workbook |
| Income | BRB statements, one row per period (Key prevents duplicates) |
| Transactions | bank / card statements (Amount < 0 = money out) |
| Accounts | checking and credit cards: balance, APR, monthly minimum. Edit in the app or the sheet |
| Plan | weekly plan written by **Write plan to GridCraft** |
| Advice | every Advisor answer, for a year-end review |

## Code

- `finance/gridcraft.py`: JSON-lines control-channel client
- `finance/master.py`: sheet layout, append / replace, read back
- `finance/parsers/brb.py`, `finance/parsers/bank.py`: statement parsers
- `finance/advisor.py`: data summary and Claude conversation for the Advisor tab
- `finance/business.py`: read-only link to the BRB owner payout planner in `../brb`
- `finance/planner.py`: week-by-week payoff / savings / investing simulation
- `ui/app.py`: the Tkinter app
