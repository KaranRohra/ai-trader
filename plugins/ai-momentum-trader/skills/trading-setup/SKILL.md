---
name: trading-setup
description: One-time setup for the AI Momentum Trader - checks Python, creates ~/ai-momentum-trader (capital, PAPER/LIVE mode), downloads NSE data, optionally backtests the strategy, and explains daily use. Use on first install, or when the user asks to set up, reset, switch PAPER/LIVE or change capital.
---

# Trading Setup

Scripts: `JOURNAL` = `<this skill's base directory>/../trade-journal/scripts/journal.py`, `SCAN` = `<base>/../momentum-scan/scripts/scan.py`, `LAB` = `<base>/../strategy-lab/scripts/lab.py`.

## Steps
1. **Python check.** Run `python3 --version`; 3.8 or newer is needed (on Windows try `python` / `py`). If it's missing, tell the user how to install it from python.org and stop. No pip packages are needed.
2. **Say this to the user first, plainly:**
   - This is an experiment, not investment advice. Trading can lose money.
   - Use a **separate Zerodha account**, or keep only the money you are willing to give the bot in it. The Kite connection can see and trade the whole account. The bot is told to touch only its own positions, but the account balance is your real backstop.
   - The bot starts in **PAPER** mode: real data, simulated orders.
3. **Ask** (AskUserQuestion): the capital to allocate (default ₹1,00,000), and the mode (PAPER, recommended, or LIVE). Tell them the default risk limits: no new entries after a −8% week, and a halt on new entries if equity falls 30% below its highest level. They can change these in `config.json` → `limits`.
4. `python3 JOURNAL init --capital <amount> --mode <PAPER|LIVE>`.
5. **Data:** `python3 SCAN update`. This downloads about 15 months of free NSE end-of-day files, takes ~2 minutes the first time, and after that one file per day.
6. **Test scan:** `python3 SCAN scan --equity <capital> --no-shadow`. Show the regime and how many signals there are today.
7. **Optional backtest** (recommended once): ask whether they want to replay the rules over history. It downloads ~10 years of NSE files (~20–40 minutes, ~200 MB) and runs `python3 SCAN backtest --from 2016-01-01`. Then run `python3 LAB record-backtest --summary <output_folder>/summary.json` so the rollback check knows the version's historical worst drawdown. Show CAGR, max drawdown, win rate and the NIFTY 500 comparison, in a neutral tone with costs included.
8. **Kite:** call the Kite MCP `login` tool, let them click the link, then `get_profile`. That confirms the connection works.
9. **Daily use, tell them:** every trading day around 9:20–10:30 AM IST, run `/trading-day` and click the Kite login link. Everything else is automatic. Stops sit at Zerodha as GTT orders, so the computer doesn't need to stay on.

## Switching to LIVE later
Only when the user explicitly asks:
1. Confirm in a question that real orders will be placed. Show their capital and risk per trade.
2. Edit `~/ai-momentum-trader/config.json` and set `"mode": "LIVE"`.
3. Paper positions are not carried into LIVE. Close them in the journal first (`JOURNAL close` at the current LTP, reason `paper_to_live`).
