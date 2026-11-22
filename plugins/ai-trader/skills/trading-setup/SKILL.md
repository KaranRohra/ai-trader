---
name: trading-setup
description: One-time setup for the AI Trader - checks Python, creates ~/ai-trader (capital, PAPER/LIVE mode), connects the broker (Zerodha ready-made, any broker with an MCP, or Manual mode), downloads NSE data, has the user choose a strategy and answer its risk questions, and explains daily use. Use on first install, or when the user asks to set up, reset, switch PAPER/LIVE or change capital.
---

# Trading Setup

Scripts: `JOURNAL` = `<this skill's base directory>/../trade-journal/scripts/journal.py`, `SCAN` = `<base>/../scanner/scripts/scan.py`, `STRAT` = `<base>/../strategies/scripts/strategy.py`, `LAB` = `<base>/../strategy-lab/scripts/lab.py`, `BROKER` = `<base>/../brokers/scripts/broker.py`.

## Steps
1. **Python check.** Run `python3 --version`; 3.8 or newer is needed (on Windows try `python` / `py`). If it's missing, tell the user how to install it from python.org and stop. No pip packages are needed.
2. **Say this to the user first, plainly:**
   - This is an experiment, not investment advice. Trading can lose money.
   - Use a **separate broker account**, or keep only the money you are willing to give the bot in it. The broker's MCP connection can see and trade the whole account. The bot only touches its own positions, but the account balance is your real backstop.
   - The bot starts in **PAPER** mode: real data, simulated orders.
3. **Ask** (AskUserQuestion): the capital to allocate (default ₹1,00,000), the mode (PAPER, recommended, or LIVE), and the broker. Options come from `python3 BROKER catalog`: **Zerodha** (ready-made), **Another broker with an official MCP**, and **Any other broker / no MCP** (Manual mode). For the second, ask a follow-up question listing Dhan, FYERS, Groww and Upstox. Each option says whether that broker's MCP can place orders and that it isn't tested by the author yet.
4. `python3 JOURNAL init --capital <amount> --mode <PAPER|LIVE>` (Zerodha is the default broker).
   **Another broker:** run the `brokers` skill's connect flow now. For Dhan and FYERS the MCP comes with the plugin, so the user only signs in through `/mcp` (Dhan also has a ready-made profile, so there's no tool matching). Groww is added once as a claude.ai connector. Any other broker's MCP is added for the user. Groww and the others need one Claude Code restart. Either way it saves a broker profile. If a restart is needed, tell the user to run `/ai-trader:trading-setup` again afterwards; the steps already done are skipped (`init` says "already set up", and the broker draft resumes). A newly connected, untested broker starts in PAPER.
5. **Data:** `python3 SCAN update`. This downloads about 15 months of free NSE end-of-day files, takes ~2 minutes the first time, and after that one file per day.
6. **Strategy and its rules:** run the `strategies` skill's setup flow:
   - pick a ready-made strategy or describe your own;
   - see what it does, with real recent examples;
   - keep or change its settings;
   - answer every risk question (nothing assumed);
   - add any more rules;
   - optionally backtest it on years of past data (the user's choice);
   - review screen → Start.
   The bot can't place a trade until this is done.
7. **Test scan:** `python3 SCAN scan --equity <capital> --no-shadow`. Show the strategy, the market filter, and how many signals there are today.
8. **Broker connection:** log in as `trading-day` step 2 describes (Zerodha: the `login` link, then `get_profile`). That confirms the connection works. Manual mode with no MCP: skip this step.
9. **Daily use, tell them:** every trading day around 9:20–10:30 AM IST, run `/trading-day` and log in to the broker when asked. Everything else is automatic. How stops are kept depends on the broker (`BROKER capability` → `protection_words`): with a GTT/OCO they sit at the broker, so the computer doesn't need to stay on. With a day stop-loss order or bot-watched stops, they only protect on days the bot runs, so say that clearly. In Manual mode, the bot lists the orders and the user places them in the broker's app. To change broker later, ask for the `brokers` skill. To see, change or switch strategies or risk rules later, ask for the `strategies` skill. To test any strategy on past data any time, run `/ai-trader:backtest`.

## Switching to LIVE later
Only when the user explicitly asks:
1. Confirm in a question that real orders will be placed. Show their capital, the active strategy and its risk answers in plain words (`STRAT show`).
2. Edit `~/ai-trader/config.json` and set `"mode": "LIVE"`.
   With a newly connected broker that isn't tested yet (`BROKER capability` → `first_live_order_pending`), say that the first real order will be shown to them before it's placed.
3. Paper positions are not carried into LIVE. Close them in the journal first (`JOURNAL close` at the current LTP, reason `paper_to_live`, `--via paper`).
