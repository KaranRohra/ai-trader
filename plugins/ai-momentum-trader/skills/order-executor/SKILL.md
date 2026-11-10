---
name: order-executor
description: Exact Kite (Zerodha) MCP procedures for the AI Momentum Trader - LIMIT CNC entries tagged CLAUDEBOT, two-leg GTT (stop + target) placement, exits, reconciliation, PAPER-mode simulation and error handling. Use whenever the bot places, changes or checks an order.
---

# Order Executor

Kite MCP tools used: `get_quotes`, `get_ltp`, `get_margins`, `get_holdings`, `get_orders`, `get_order_history`, `place_order`, `cancel_order`, `get_gtts`, `place_gtt_order`, `modify_gtt_order`, `delete_gtt_order`.

**Mode.** Read `mode` from `~/ai-momentum-trader/config.json`. In **PAPER** mode, never call `place_order`, `place_gtt_order`, `modify_gtt_order`, `delete_gtt_order` or `cancel_order`; use quotes only. In **LIVE** mode follow the steps exactly.

**Prices.** Prices must be on NSE's tick grid. The scan already rounds `limit_price` and stops. For a price you compute yourself, round **down** to a multiple of 0.05 (under ₹1,000), 0.10 (₹1,000–5,000), 0.50 (₹5,000–10,000) or 1.00 (above that). If Kite rejects a tick size, round to the next coarser step and retry once.

## Entry
**LIVE:**
1. `place_order`: exchange NSE, tradingsymbol, transaction_type BUY, quantity, product **CNC**, order_type **LIMIT**, price = `limit_price`, variety regular, validity DAY, tag **CLAUDEBOT**.
2. Poll `get_order_history` (or `get_orders`) every ~30 s for up to 3 minutes.
   - COMPLETE: the fill is `average_price`.
   - Partially filled after 3 minutes: `cancel_order` the rest and continue with the filled qty.
   - Nothing filled: `cancel_order` and log `unfilled`.
   - REJECTED: log the reason, don't retry the same order, and tell the user.
3. **Immediately** place stop + target as ONE two-leg (OCO) GTT with `place_gtt_order`:
   - trigger_type **two-leg**, transaction_type SELL, exchange NSE, product CNC, last_price = current LTP.
   - **Stop leg:** stop = `stop_level` if the scan gives one (chart-based stop modes), otherwise fill − `stop_distance` (the default 14% stop), tick-rounded down. lower_trigger_value = stop, lower_limit_price = stop × 0.98 (tick-rounded down) so it fills in a fast fall, lower_quantity = filled qty.
   - **Target leg:** target = fill + `target_r` × (fill − stop), tick-rounded up. upper_trigger_value = target, upper_limit_price = target × 0.995 (tick-rounded down), upper_quantity = filled qty.
   - Kite requires stop < LTP < target. If the LTP is already at or below the stop, sell at MARKET instead and journal `stop`.
   - Save the returned GTT id.
4. If the GTT fails twice, sell the position at MARKET (CNC, tag CLAUDEBOT) and journal the close with reason `gtt_failed`.
5. `journal.py open --symbol X --qty <filled> --price <avg fill> --stop <stop> --target <target> --gtt-id <id> --order-id <id> --rank R --score S`.

**PAPER:**
1. `get_quotes`. If `ohlc.open` is above `skip_if_open_above`, log `skipped_gap`.
2. If the LTP is at or below `limit_price`, the fill is the LTP. Otherwise log `unfilled`.
3. Stop = `stop_level` (or fill − `stop_distance` if there is no `stop_level`); target = fill + `target_r` × (fill − stop). Then `journal.py open --stop --target` (no GTT id).

## Exit (manual, or a rule exit if one is ever enabled in config)
**LIVE:**
1. `delete_gtt_order` for the position's GTT. If the GTT is already triggered or gone, check holdings first.
2. `place_order`: SELL, product CNC, order_type MARKET, quantity = the bot's qty only, tag CLAUDEBOT.
3. Confirm the fill, then `journal.py close --symbol X --price <avg fill> --reason <reason> --order-id <id>`.

**PAPER:** `journal.py close` at the current LTP.

## Trail (only if `trail_atr` > 0 in config; off by default)
- **LIVE:** `modify_gtt_order` on the same GTT id: lower leg trigger = `new_stop`, limit = new_stop × 0.98, keep the target leg and qty unchanged. If modify fails, delete the GTT and place a new one, saving the new id. Then `journal.py stop --symbol X --stop <new_stop> --gtt-id <id if changed>`.
- **PAPER:** `journal.py stop` only.

## Reconcile (LIVE, start of every run)
For each position in `positions.json`:
1. Look it up in `get_holdings` (T1 + settled quantity) and in `get_gtts` (by id).
2. Held, with the GTT active: OK.
3. Not held (or the qty dropped), with the GTT triggered: find the sell in `get_orders` / order history, or use the triggered leg's price. Then `journal.py close --reason stop` or `--reason target` (whichever leg fired) at the actual fill price.
4. Held, but the GTT is missing, cancelled or expired (GTTs expire after 1 year): place a new GTT at the journal's stop and update the id.
5. Qty mismatch you can't explain: don't trade that symbol, and tell the user.

Holdings that aren't in `positions.json` belong to the user. Ignore them completely.
