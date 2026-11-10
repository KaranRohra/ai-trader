---
name: weekly-review
description: Weekly report for the AI Trader - week and all-time P&L, equity vs NIFTY 500, trades closed, vetoes and how lessons performed, what the bot noticed. Use on weekends or when the user asks for the weekly review, results, or "how did the bot do".
---

# Weekly Review

Scripts: `JOURNAL` = `<base>/../trade-journal/scripts/journal.py`, `LAB` = `<base>/../strategy-lab/scripts/lab.py`, `SCAN` = `<base>/../momentum-scan/scripts/scan.py`.

1. `python3 SCAN outcomes` to bring forward returns up to date.
2. `python3 JOURNAL weekly`. This defaults to the current week; pass `--week` for an earlier one.
3. `python3 LAB veto-report` and `python3 LAB features --min-n 10`. These are context only; don't propose changes here (that's `strategy-lab`).
4. NIFTY 500 for the same week: the first and last values in `~/ai-trader/data/index_nifty500.json` for those dates.
5. Write `~/ai-trader/weekly/<week>.md` with:
   - **Numbers:** start → end equity, week P&L (₹ and %), since-start P&L, max drawdown, NIFTY 500 week %, and mode (PAPER/LIVE).
   - **Trades closed:** symbol, entry → exit, R, P&L, exit reason.
   - **Open positions:** entry, stop, unrealized P&L.
   - **Decisions:** taken / vetoed / skipped counts, with each veto reason.
   - **What the bot noticed:** 2–4 factual observations from the data (e.g. "3 of 4 entries came in one sector"). No predictions.
   - **Learning status:** pending suggestions, active trial, veto verdict.
6. Show the user a short version, with the file path.

**Tone.** Neutral and factual. Report losses as plainly as gains. Never say a stock or the strategy "will" do anything, and never present it as advice.
