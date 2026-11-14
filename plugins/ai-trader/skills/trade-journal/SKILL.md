---
name: trade-journal
description: The AI Trader's books - a detailed record of every trade from signal to exit (why it was bought, plan, fill, charges, risk, broker stop, best/worst price while open, exit, net P&L, R-multiple), every order/GTT/stop event, every decision (taken, vetoed, skipped) with the signal's numbers, and daily equity. Use to log anything the bot does, or when the user asks about a trade, positions, P&L, charges, history or why a trade was skipped.
---

# Trade Journal

The bot keeps its **own** books in `~/ai-trader/journal/`, separate from the rest of the Zerodha account. All writes go through `scripts/journal.py`; never edit the CSV/JSON files by hand.

## Files
| File | One row per | What's in it |
|---|---|---|
| `trades.csv` | trade | Written when the buy fills (status `OPEN`), updated every run, finished at the exit (`CLOSED`). Columns, in groups: **trade** (id, symbol, sector, strategy and version, mode) · **why bought** (signal date and close, rank, score, rules passed, RSI, volume ratio, breakout %, extension %, ATR, turnover, SMA 50/200, 6-month return, market filter, NIFTY 500, Claude's check note) · **plan** (limit, skip-if-open-above, planned qty/stop/target, sizing rule) · **entry** (date, time, order id, qty, avg fill, day open, fill vs limit, fill vs signal close, value, STT, exchange, SEBI, stamp, GST, total charges, cost) · **risk at entry** (stop and stop %, risk per share, ₹ at risk, % of equity at risk, target and target %, reward:risk, position % of equity, equity, open positions) · **broker protection** (GTT id and type, current stop, stop moves, last move date) · **while open** (last price, unrealized ₹ and %, sessions held, highest close, best and worst price with date, % and R) · **exit** (date, time, order id, via, reason, trigger price, fill, slippage, value, charges itemized incl. DP) · **result** (gross P&L, total charges, net P&L, return %, net return %, R-multiple, net R, days held, % of best move captured, win/loss, equity after) · **exit note** |
| `trade_events.csv` | event | Every step of every trade with date and time: `signal`, `order_placed`, `order_partial`, `order_cancelled`, `order_rejected`, `entry_filled`, `gtt_placed`, `gtt_modified`, `gtt_deleted`, `gtt_replaced`, `gtt_triggered`, `stop_moved` (old → new, why), `exit_order_placed`, `exit_filled`, `closed`, `error`, `note` |
| `decisions.csv` | signal decision | taken / vetoed / skipped / blocked, with the reason and the signal's numbers (close, rank, score, sector, limit, stop, target, qty, value) |
| `daily.csv` | run day | equity, cash, invested, unrealized, realized, day P&L, week P&L, peak, drawdown, open risk (₹ lost if every stop hit now), charges paid, trades closed, NIFTY 500 close |

## Commands
| Command | When |
|---|---|
| `status [--prices JSON]` | start of every run: equity, cash, week P&L, open positions, `new_entries_blocked` |
| `decision --symbol X --decision D --reason "..."` | every signal not taken: `vetoed`, `skipped_gap`, `skipped_slots`, `skipped_cash`, `unfilled`, `blocked_regime`, `blocked_limits`, `blocked_strategy_rule`. The signal's numbers are filled in from the latest scan |
| `event --symbol X --event E [--price] [--qty] [--order-id] [--gtt-id] [--old] [--new] [--note] [--time]` | each order / GTT step that `open`, `stop` and `close` don't log themselves: `order_placed`, `order_partial`, `order_cancelled`, `order_rejected`, `gtt_modified`, `gtt_deleted`, `gtt_replaced`, `exit_order_placed`, `error`, `note`. Events logged before the fill attach to the trade when it opens |
| `open --symbol X --qty N --price P [--stop S] [--target T] --strategy ID [--gtt-id] [--order-id] --time HH:MM:SS [--day-open P] --note "..."` | after the fill. `--price` = average fill, `--time` = the order's exchange time, `--note` = the judgment checks done (one line, e.g. "no results within 5 days; no adverse news; Kite close matched scan"). Signal, plan and risk columns come from the scan automatically. Also logs `taken`, `signal`, `entry_filled`, `gtt_placed` |
| `stop --symbol X --stop S [--gtt-id] --reason R [--highest-close C]` | after moving a stop. `--reason`: `trail`, `breakeven`, or the strategy's rule name. Refuses a lower stop if the strategy's stops only move up. Logs `stop_moved` |
| `close --symbol X --price P --reason R --via V [--trigger-price P] [--order-id] [--time] [--note] [--date]` | after the exit. `--price` = average exit fill; `--via` = `gtt_stop`, `gtt_target`, `market_sell` or `paper`; `--trigger-price` = the GTT trigger (or the price the exit was planned at), for slippage. Logs `gtt_triggered`, `exit_filled`, `closed` |
| `snapshot --prices JSON` | end of every run: updates each open trade's live columns (last price, unrealized, best/worst since entry from NSE data, sessions held) and writes the day's row in `daily.csv` |
| `trade --symbol X` or `trade --id ID` | the user asks about a trade: the whole row by group plus its events |
| `trades [--status OPEN|CLOSED] [--strategy ID]` | a short list of trades |
| `weekly [--week 2026-W45]` | weekly stats, also saved to `weekly/<week>.json` |

When the user asks about a trade, run `trade` and present it as a short trade card (why bought, plan vs fill, risk, broker stop, while open, exit and result, then the events as a timeline). Use only the numbers in the journal.

**Reason format for vetoes:** `<code>: <evidence>`, for example `results_within_5_days: Q2 results on 2026-11-05 (company filing)`. The learning loop groups vetoes by the code before the colon, so keep the codes from the `trading-day` list.

**Charges** are itemized estimates for Zerodha equity delivery: brokerage ₹0, STT 0.1% each side, NSE transaction charge 0.00297% each side, SEBI ₹10 per crore, stamp duty 0.015% on buys, GST 18% on brokerage + exchange + SEBI, DP charge ₹15.34 per stock per sell day. Rates change; override any of them in `~/ai-trader/config.json` → `"charges"` (keys: `brokerage_per_order`, `stt_pct`, `exchange_pct`, `sebi_per_crore`, `stamp_buy_pct`, `gst_pct`, `dp_per_sell`). The contract note is the final word.

**R-multiple.** (exit − entry) ÷ (entry − initial stop); −1R means a full stop-out. **Net R** = net P&L after charges ÷ ₹ at risk.

**Best / worst price** = the highest high and lowest low from the day after entry (official NSE data), and the exit price. **Captured % of best** = (exit − entry) ÷ (best − entry).

**Upgrade from an older journal.** The first command after updating converts it once: the old files are copied to `journal/backup_before_v2.1/`, closed trades keep their numbers (charges weren't itemized then), and each open position becomes an `OPEN` trade filled in from the bot's saved scan, positions and decisions.
