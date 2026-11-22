# Zerodha (Kite MCP): exact tool use

Tested LIVE. The `order-executor` skill says *what* to do; this file says *how* on Kite. The MCP server is the plugin's bundled `kite` server (`https://mcp.kite.trade/mcp`). It may also appear as a Zerodha Kite connector; use whichever is available.

## Login (every run)
Call `login` and show the link it returns as a clickable markdown link. Wait for the user to say they're done, then call `get_profile` and confirm the user name. Zerodha needs a fresh login every day.

## Prices and ticks
NSE tick grid: round a price you compute yourself **down** to a multiple of 0.05 (under ₹1,000), 0.10 (₹1,000–5,000), 0.50 (₹5,000–10,000) or 1.00 (above that). If Kite rejects the tick size, round to the next coarser step and retry once.

## Reading
- Quote: `get_quotes` with `NSE:SYMBOL` → `last_price`, `ohlc.open`, `ohlc.close` (previous close), `upper_circuit_limit`.
- LTP only: `get_ltp`.
- Cash: `get_margins` → `equity` → `available.live_balance` (or `net`).
- Holdings: `get_holdings` → quantity + `t1_quantity` per `tradingsymbol`.
- Orders: `get_orders`; one order: `get_order_history` with `order_id` → the last row has `status` (COMPLETE / OPEN / CANCELLED / REJECTED), `filled_quantity`, `average_price`, `exchange_timestamp`, `status_message`.

## Buy (entry)
`place_order`: `exchange` NSE, `tradingsymbol`, `transaction_type` BUY, `quantity`, `product` CNC, `order_type` LIMIT, `price` = limit price, `variety` regular, `validity` DAY, `tag` CLAUDEBOT.
Cancel: `cancel_order` with `variety` regular and `order_id`.

## Market sell (exit)
`place_order`: SELL, `product` CNC, `order_type` MARKET, `quantity` = the bot's qty only, `variety` regular, `tag` CLAUDEBOT.

## Day stop-loss order (only if the profile's protection is `day_sl`)
`place_order`: SELL, CNC, `order_type` SL, `trigger_price` = stop, `price` = stop × 0.98 (tick-rounded down), `validity` DAY, `tag` CLAUDEBOT.

## GTT (protection `two_leg` or `single`)
`place_gtt_order`: `transaction_type` SELL, `exchange` NSE, `tradingsymbol`, `product` CNC, `last_price` = current LTP, `quantity` = filled qty.
- **Two-leg (OCO)**, `trigger_type` two-leg: `lower_trigger_value` = stop, `lower_limit_price` = stop × 0.98 (tick-rounded down); `upper_trigger_value` = target, `upper_limit_price` = target × 0.995 (tick-rounded down).
- **Single**, `trigger_type` single: `trigger_value` = stop, `limit_price` = stop × 0.98. For a target-only GTT: the target and target × 0.995.
- Kite requires stop < LTP < target. If the LTP is already at or below the stop, sell at MARKET instead.
- The response has the GTT id (`trigger_id`). Save it.
- Modify: `modify_gtt_order` on the same id, same `trigger_type`, with the new values (keep the qty and any target leg).
- Delete: `delete_gtt_order` with the id.
- List: `get_gtts` → each has `id`, `status` (active / triggered / cancelled / expired / deleted), and its `orders`. A triggered GTT's sell shows up in `get_orders` with the same tradingsymbol.
- GTTs expire after 1 year. Reconcile replaces an expired one.
