---
name: risk-manager
description: Hard risk rules and position-sizing math for the AI Trader - 2% risk per trade, 14% stops with 1:2 targets, max 10 positions, weekly loss limit, halt level, CNC only, bot-only positions. Read before placing any order; use when the user asks about risk, sizing or limits.
---

# Risk Manager

These rules are **locked**. They can't be changed by the strategy-lab or by Claude during a run. Only the user can change them, by editing `~/ai-trader/config.json` themselves.

## Hard rules
1. **Only NSE cash equity, delivery (CNC).** Never MIS, MTF, NRML, F&O, BSE, AMO or short selling.
2. **Only the bot's own positions.** The bot's positions are the ones in `positions.json`, and its orders are tagged `CLAUDEBOT`. Never sell, modify or delete a GTT for any other holding, even if it has the same symbol: if the user already owns a stock outside the bot, the bot does not buy it (log `vetoed: already_held_outside_bot`).
3. **Every position has a broker-side GTT stop.** If the GTT can't be placed after 2 attempts, sell the position immediately.
4. **Stops only move up.** `journal.py stop` refuses a lower stop.
5. **At most 10 open positions.** There is no limit on entries per day. Cash usually caps it at ~7, since each position is ~14.3% of equity.
6. **Weekly loss limit.** If the week's P&L is −8% or worse, there are no new entries until next Monday. `journal.py status` reports this as `new_entries_blocked`.
7. **Halt.** If bot equity is 30% or more below its highest level so far (`drawdown_pct` in `journal.py status`; the peak starts at the starting capital), there are no new entries. That is 1.5× the backtest's worst drawdown (−20.4%), so it means the strategy is no longer behaving as tested. Tell the user clearly at the top of the summary. Exits and stops keep running.
8. **Regime.** If NIFTY 500 is below its 200-DMA, there are no new entries.
9. **When in doubt, don't trade.** For an unknown error, odd data or an unexpected holding, skip the trade, log why, and tell the user.

## Sizing (2% risk, 14% stop)
- `risk_amount = equity × risk_pct / 100`. With ₹1,00,000 equity that's ₹2,000. Equity comes from `journal.py status`: bot capital + realized P&L + open positions at market.
- `stop_distance` = 14% of the signal close (from the scan). After the fill, stop = fill − `stop_distance`.
- `qty = floor(risk_amount ÷ stop_distance)`.
- Cap: `qty × limit_price` may be at most `max_position_pct` (100%) of equity, and at most the free cash. In LIVE that's Kite `get_margins` → equity → available cash. In PAPER it's `journal.py status` → `cash`.
- If qty < 1, skip with `skipped_cash`.
- The scan's `qty_est` already applies this with the equity you passed. Recompute only if the cash check changes it.

**Worked example.** Equity ₹1,00,000, risk ₹2,000. Signal close ₹990, limit ₹1,000, stop distance 14% × 990 = ₹138.6 → qty = floor(2,000 ÷ 138.6) = 14 (₹14,000). If it fills at ₹995: stop = 995 − 138.6 = ₹856.40 and target = 995 + 2 × 138.6 = ₹1,272.20, tick-rounded.
