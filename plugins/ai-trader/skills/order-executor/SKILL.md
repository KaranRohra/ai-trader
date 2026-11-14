---
name: order-executor
description: Exact Kite (Zerodha) MCP procedures for the AI Trader - LIMIT CNC entries tagged CLAUDEBOT, broker-side GTT stops and targets (two-leg, stop-only or target-only, as the strategy defines), trailing-stop updates, exits, reconciliation, PAPER-mode simulation and error handling. Use whenever the bot places, changes or checks an order.
---

# Order Executor

Kite MCP tools used: `get_quotes`, `get_ltp`, `get_margins`, `get_holdings`, `get_orders`, `get_order_history`, `place_order`, `cancel_order`, `get_gtts`, `place_gtt_order`, `modify_gtt_order`, `delete_gtt_order`.

**Mode.** Read `mode` from `~/ai-trader/config.json`. In **PAPER** mode, never call `place_order`, `place_gtt_order`, `modify_gtt_order`, `delete_gtt_order` or `cancel_order`; use quotes only. In **LIVE** mode follow the steps exactly.

**Prices.** Prices must be on NSE's tick grid. The scan already rounds `limit_price` and stops. For a price you compute yourself, round **down** to a multiple of 0.05 (under ₹1,000), 0.10 (₹1,000–5,000), 0.50 (₹5,000–10,000) or 1.00 (above that). If Kite rejects a tick size, round to the next coarser step and retry once.

## Entry
**LIVE:**
1. `place_order`: exchange NSE, tradingsymbol, transaction_type BUY, quantity, product **CNC**, order_type **LIMIT**, price = `limit_price`, variety regular, validity DAY, tag **CLAUDEBOT**. Then `journal.py event --symbol X --event order_placed --price <limit> --qty <qty> --order-id <id>`.
2. Poll `get_order_history` (or `get_orders`) every ~30 s for up to 3 minutes.
   - COMPLETE: the fill is `average_price`.
   - Partially filled after 3 minutes: `cancel_order` the rest (`event --event order_partial --qty <filled>`) and continue with the filled qty.
   - Nothing filled: `cancel_order` (`event --event order_cancelled`) and log the decision `unfilled`.
   - REJECTED: `event --event order_rejected --note "<Kite's reason>"`, don't retry the same order, and tell the user.
   - Note the fill's `exchange_timestamp` time (HH:MM:SS) for the journal.
3. **Work out the stop and target from the fill** (the signal says which kinds the strategy uses):
   - **Stop:** `stop_level` if the scan gives one (chart-level stops), otherwise fill − `stop_distance` (pct and ATR stops), tick-rounded down. No `stop_type` = the strategy has no stop.
   - **Target:** `target_type` r → fill + `target_value` × (fill − stop), `target_type` pct → fill × (1 + `target_value`/100), tick-rounded up. No `target_type` = no target.
4. **Immediately** place them at Zerodha with `place_gtt_order` (transaction_type SELL, exchange NSE, product CNC, last_price = current LTP, quantity = filled qty):
   - **Stop and target:** ONE **two-leg** (OCO) GTT. lower_trigger_value = stop, lower_limit_price = stop × 0.98 (tick-rounded down) so it fills in a fast fall; upper_trigger_value = target, upper_limit_price = target × 0.995 (tick-rounded down).
   - **Stop only** (no target, e.g. trailing-stop strategies): a **single** GTT, trigger_value = stop, limit_price = stop × 0.98.
   - **Target only** (no stop allowed by the user's risk answers): a single GTT at the target, limit_price = target × 0.995.
   - **Neither:** no GTT. The position is closed only by the strategy's exit rules.
   - Kite requires stop < LTP < target. If the LTP is already at or below the stop, sell at MARKET instead and journal `stop`.
   - Save the returned GTT id. A failed attempt: `event --event error --note "<what failed>"`.
5. If a GTT with a stop fails twice and the strategy's risk has `broker_stop_required: true`, sell the position at MARKET (CNC, tag CLAUDEBOT) and journal the close with reason `gtt_failed`. If it's false, keep the position, journal it without a GTT id and tell the user.
6. `journal.py open --symbol X --qty <filled> --price <avg fill> [--stop <stop>] [--target <target>] --strategy <id> --gtt-id <id> --order-id <id> --time <fill time> --day-open <quote ohlc.open> --note "<the judgment checks done>"`. The journal fills in the signal, plan and risk columns from the scan.

**PAPER:**
1. `get_quotes`. If `ohlc.open` is above `skip_if_open_above`, log `skipped_gap`.
2. If the LTP is at or below `limit_price`, the fill is the LTP. Otherwise log `unfilled`.
3. Stop and target as in LIVE step 3. Then `journal.py open ... --stop --target --strategy --time <now> --day-open <ohlc.open> --note "..."` (no GTT id, no order id).

## Exit (a strategy exit rule, a time exit, or a manual close)
**LIVE:**
1. `delete_gtt_order` for the position's GTT (`event --event gtt_deleted --gtt-id <id>`). If the GTT is already triggered or gone, check holdings first.
2. `place_order`: SELL, product CNC, order_type MARKET, quantity = the bot's qty only, tag CLAUDEBOT (`event --event exit_order_placed --order-id <id>`).
3. Confirm the fill, then `journal.py close --symbol X --price <avg fill> --reason <reason> --via market_sell --trigger-price <LTP when you decided to sell> --order-id <id> --time <fill time> --note "<one line: what the rule saw>"`.

**PAPER:** `journal.py close ... --via paper` at the current LTP.

## Trail (when the scan says `raise_stop` or `lower_stop` for a position)
`lower_stop` only appears when the strategy's risk has `stops_only_up: false`.
- **LIVE:** `modify_gtt_order` on the same GTT id: stop trigger = `new_stop`, limit = new_stop × 0.98; keep any target leg and the qty unchanged. If modify fails, delete the GTT and place a new one, saving the new id (`event --event gtt_replaced --gtt-id <new id> --old <old id>`). If the position had no GTT yet, place a single stop GTT. Then `journal.py stop --symbol X --stop <new_stop> --gtt-id <id if changed> --reason <trail | breakeven | the strategy's rule>`.
- **PAPER:** `journal.py stop ... --reason ...` only.

## Reconcile (LIVE, start of every run)
For each position in `positions.json`:
1. Look it up in `get_holdings` (T1 + settled quantity) and in `get_gtts` (by id).
2. Held, with the GTT active: OK.
3. Not held (or the qty dropped), with the GTT triggered: find the sell in `get_orders` / order history, or use the triggered leg's price. Then `journal.py close --reason stop --via gtt_stop` or `--reason target --via gtt_target` (whichever leg fired) with `--price <actual avg fill> --trigger-price <that leg's trigger> --order-id <sell order id> --time <fill time> --date <fill date>`.
4. Held, but the GTT is missing, cancelled or expired (GTTs expire after 1 year): place a new GTT at the journal's stop (and target, if any) and record it: `journal.py event --symbol X --event gtt_replaced --gtt-id <new id> --note "<why>"` (this also updates the id). Positions the journal shows with no stop and no target never had a GTT; that's expected.
5. Qty mismatch you can't explain: don't trade that symbol, and tell the user.

Holdings that aren't in `positions.json` belong to the user. Ignore them completely.
