---
name: trading-setup
description: One-time setup for the AI Trader - checks Python, creates ~/ai-trader (capital, PAPER/LIVE mode), downloads NSE data, has the user choose a strategy and answer its risk questions, and explains daily use. Use on first install, or when the user asks to set up, reset, switch PAPER/LIVE or change capital.
---

# Trading Setup

Scripts: `JOURNAL` = `<this skill's base directory>/../trade-journal/scripts/journal.py`, `SCAN` = `<base>/../scanner/scripts/scan.py`, `STRAT` = `<base>/../strategies/scripts/strategy.py`, `LAB` = `<base>/../strategy-lab/scripts/lab.py`.

## Steps
1. **Python check.** Run `python3 --version`; 3.8 or newer is needed (on Windows try `python` / `py`). If it's missing, tell the user how to install it from python.org and stop. No pip packages are needed.
2. **Say this to the user first, plainly:**
   - This is an experiment, not investment advice. Trading can lose money.
   - Use a **separate Zerodha account**, or keep only the money you are willing to give the bot in it. The Kite connection can see and trade the whole account. The bot only touches its own positions, but the account balance is your real backstop.
   - The bot starts in **PAPER** mode: real data, simulated orders.
3. **Ask** (AskUserQuestion): the capital to allocate (default ₹1,00,000) and the mode (PAPER, recommended, or LIVE).
4. `python3 JOURNAL init --capital <amount> --mode <PAPER|LIVE>`.
5. **Data:** `python3 SCAN update`. This downloads about 15 months of free NSE end-of-day files, takes ~2 minutes the first time, and after that one file per day.
6. **Strategy and its rules:** run the `strategies` skill's setup flow:
   - pick a ready-made strategy or describe your own;
   - see what it does, with real recent examples;
   - keep or change its settings;
   - answer every risk question (nothing assumed);
   - add any more rules;
   - review screen → Start.
   The bot can't place a trade until this is done.
7. **Test scan:** `python3 SCAN scan --equity <capital> --no-shadow`. Show the strategy, the market filter, and how many signals there are today.
8. **Kite:** call the Kite MCP `login` tool, let them click the link, then `get_profile`. That confirms the connection works.
9. **Daily use, tell them:** every trading day around 9:20–10:30 AM IST, run `/trading-day` and click the Kite login link. Everything else is automatic. Stops and targets sit at Zerodha as GTT orders, so the computer doesn't need to stay on. To see, change or switch strategies or risk rules later, ask for the `strategies` skill.

## Switching to LIVE later
Only when the user explicitly asks:
1. Confirm in a question that real orders will be placed. Show their capital, the active strategy and its risk answers in plain words (`STRAT show`).
2. Edit `~/ai-trader/config.json` and set `"mode": "LIVE"`.
3. Paper positions are not carried into LIVE. Close them in the journal first (`JOURNAL close` at the current LTP, reason `paper_to_live`).
