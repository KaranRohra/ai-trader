---
name: trade-journal
description: The AI Trader's books - bot-only positions, every decision (taken, vetoed, skipped) with its reason, closed trades with P&L and R-multiples, and daily equity. Use to log anything the bot does, or when the user asks about positions, P&L, history or why a trade was skipped.
---

# Trade Journal

The bot keeps its **own** books in `~/ai-trader/`, separate from the rest of the Zerodha account. All writes go through `scripts/journal.py`; never edit the CSV/JSON files by hand.

| Command | When |
|---|---|
| `journal.py status [--prices JSON]` | start of every run: equity, cash, week P&L, open positions, `new_entries_blocked` |
| `journal.py decision --symbol X --decision D --reason "..."` | every signal not taken: `vetoed`, `skipped_gap`, `skipped_slots`, `skipped_cash`, `unfilled`, `blocked_regime`, `blocked_limits` |
| `journal.py open --symbol X --qty N --price P [--stop S] [--target T] [--strategy ID] [--gtt-id] [--order-id] [--rank] [--score]` | after a fill (also logs `taken`); the position remembers its strategy |
| `journal.py stop --symbol X --stop S [--gtt-id] [--highest-close]` | after moving a stop (refuses to lower it if the strategy's risk says stops only move up) |
| `journal.py close --symbol X --price P --reason R [--order-id] [--date]` | after an exit: computes P&L after estimated costs and the R-multiple |
| `journal.py snapshot --prices JSON` | end of every run: daily equity |
| `journal.py weekly [--week 2026-W45]` | weekly stats, also saved to `weekly/<week>.json` |

**Reason format for vetoes:** `<code>: <evidence>`, for example `results_within_5_days: Q2 results on 2026-11-05 (company filing)`. The learning loop groups vetoes by the code before the colon, so keep the codes from the `trading-day` list.

**Costs.** Estimated Zerodha delivery costs: ~0.12% per side (STT, stamp duty, exchange charges, GST) plus ₹16 DP charge per sell. Brokerage is ₹0.

**R-multiple.** (exit − entry) ÷ (entry − initial stop). −1R means a full stop-out.
