---
name: risk-manager
description: Risk rules and position-sizing math for the AI Trader - each strategy's own risk settings chosen by the user (sizing, max positions, weekly loss limit, drawdown halt, market filter, broker-side stop, stops only up) plus the one rule for every strategy (bot-only positions). Read before placing any order; use when the user asks about risk, sizing or limits.
---

# Risk Manager

## Where the rules come from
Every strategy has its own **risk block**, answered by the user in the `strategies` skill's setup flow (risk questions → review screen → Start). It's stored in `~/ai-trader/strategies/<id>.json` → `risk`, with the date it was `confirmed`. The bot never invents, assumes or relaxes a risk rule:
- A strategy whose risk isn't confirmed is **not traded** (`journal.py status` → `block_reasons: risk_not_confirmed`; the scan says `risk_confirmed: false`).
- Claude and the strategy-lab can't change a risk answer during a run. The lab may *suggest* a change; it applies only when the user picks Apply.
- `null` means the user chose "no limit" for that rule. Respect that; don't add your own.

`python3 <base>/../strategies/scripts/strategy.py show --id <active>` → `your_risk_rules` shows the current answers.

## The one rule for every strategy
**Bot-only positions.** The bot's positions are the ones in `positions.json`, and its orders are tagged `CLAUDEBOT`. Never sell, modify or delete a GTT for any other holding, even if it has the same symbol. If the user already owns a stock outside the bot, the bot does not buy it (log `vetoed: already_held_outside_bot`).

## What the engine supports today
NSE cash equity, delivery (CNC), long only, LIMIT entries, GTT stop/target orders and MARKET exits. That's a limit of the current engine, not a risk rule: never place MIS, MTF, NRML, F&O, BSE, AMO or short orders.

## The strategy's risk rules (as the user answered them)
| Setting | What the bot does |
|---|---|
| `sizing` | `risk`: qty = floor(equity × pct ÷ stop distance). `fixed_pct`: qty = floor(equity × pct ÷ limit price) |
| `max_positions` | no new entry when this many bot positions are open (null = no limit; cash still limits it) |
| `max_position_pct` | qty × limit price at most this % of equity (null = no cap) |
| `weekly_loss_limit_pct` | week P&L at or below −this % → no new entries until next Monday (`new_entries_blocked`, reason `weekly_loss_limit`) |
| `halt_drawdown_pct` | equity this % or more below its highest level → no new entries (reason `drawdown_halt`). Tell the user clearly at the top of the summary. Exits and stops keep running |
| `regime` | the market filter fails → no new entries (scan → `regime.new_entries_allowed: false`) |
| `broker_stop_required` | true: every position gets a GTT stop at the broker right after the fill; if it can't be placed after 2 attempts, sell the position. false: the strategy may have no stop; exits come from its rules |
| `stops_only_up` | true: `journal.py stop` refuses a lower stop. false: a trailing stop may also move down |

All counts and P&L use the bot's whole book (every strategy's positions), since it's one account.

**When in doubt, don't trade.** For an unknown error, odd data or an unexpected holding, skip the trade, log why, and tell the user.

## Sizing details
- Equity comes from `journal.py status`: bot capital + realized P&L + open positions at market.
- Stop distance: `stop_distance` from the scan (pct and ATR stops, measured from the fill), or limit price − `stop_level` (chart-level stops).
- Cap: also at most the free cash. In LIVE that's Kite `get_margins` → equity → available cash; in PAPER it's `journal.py status` → `cash`.
- If qty < 1, skip with `skipped_cash`. The scan's `qty_est` already applies the sizing with the equity you passed; recompute only if the cash check changes it.

**Worked example (risk sizing, 2% of equity, 14% stop).** Equity ₹1,00,000, risk ₹2,000. Signal close ₹990, limit ₹1,000, stop distance 14% × 990 = ₹138.6 → qty = floor(2,000 ÷ 138.6) = 14 (₹14,000). If it fills at ₹995: stop = 995 − 138.6 = ₹856.40 and a 1:2 target = 995 + 2 × 138.6 = ₹1,272.20, tick-rounded.
