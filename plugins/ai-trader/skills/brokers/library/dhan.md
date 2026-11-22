# Dhan (DhanHQ MCP): exact tool use

**Ready-made.** The tool names and payloads below were read from the Dhan MCP's own tool list on 2026-11-22, and the read-only tools were called on a real account. Conditional-trigger stops have been used with real orders by the plugin's author; the bot's own buy / sell flow on Dhan hasn't been run LIVE yet, so its first LIVE order is shown to the user first. The `order-executor` skill says *what* to do; this file says *how* on Dhan.

The server is the plugin's bundled `dhan` server (`https://mcp.dhan.co/mcp`, shown in `/mcp` as `plugin:ai-trader:dhan`). It may also appear as a claude.ai Dhan connector; use whichever is signed in. Tool names are the same on both.

## How the tools work
Every Dhan tool takes `action` (a word) and `payload` (a **JSON string**, e.g. `"{\"orderId\": \"123\"}"`). Replies are formatted text, not JSON: read the values from the text. `dhanClientId` is added by the server; never pass it.

## Login (every run)
Sign-in is OAuth through `/mcp` → `plugin:ai-trader:dhan` → **Authenticate**. Check it with `portfolio_agent_tool` action `funds`. If a call (other than a quote) says unauthorized or the session expired, ask the user to Authenticate again in `/mcp`. The server also has a `login` tool that returns a browser link (show it as a clickable markdown link) and `complete_login` with the `tokenId` from the callback URL, as a fallback. There is no profile tool: the funds reply is the connection check.

## Symbols: securityId
Dhan orders use a numeric `securityId`. Resolve it with `search_agent_tool` action `search`, payload `{"query": "<SYMBOL>", "exchange": "NSE"}`, and take the row `<SYMBOL>-EQ [NSE E] securityId=<n> EQUITY` (e.g. RELIANCE → 2885). Ignore futures and options rows. Save the securityId in the trade's order event so later steps don't search again. `trading_agent_tool` also accepts `"symbol"` + `"exchangeSegment": "NSE_EQ"` instead, but pass the securityId you resolved.

## Prices and ticks
NSE tick grid: round a price you compute yourself **down** to a multiple of 0.05 (under ₹1,000), 0.10 (₹1,000–5,000), 0.50 (₹5,000–10,000) or 1.00 (above that). If Dhan rejects the tick size, round to the next coarser step and retry once.

## Reading
- **Quote:** `market_data_agent_tool` action `ohlc` (open / high / low / close + LTP) or `quote` (adds depth and circuit limits), payload `{"NSE_EQ": [<securityId>]}`. LTP only: action `ltp`, same payload. 1 request per second. **These need Dhan's Data API plan.** Without it they answer `API Error: Unauthorized`: then use the scan's prices (the LIMIT price still caps what a buy can pay) and say so once.
- **Cash:** `portfolio_agent_tool` action `funds` → `Available Balance`.
- **Holdings:** `portfolio_agent_tool` action `holdings` → per symbol `Qty: <n> (avail <n>, T1 <n>)` and `Avg Cost`. The qty held = Qty; shares bought today show under T1.
- **Today's orders:** `orderbook_agent_tool` action `list`.
- **One order:** `orderbook_agent_tool` action `get`, payload `{"orderId": "<id>"}` → its status (Dhan's statuses: TRANSIT, PENDING, PART_TRADED, TRADED, CANCELLED, REJECTED, EXPIRED), filled qty, average traded price, time and any rejection reason. TRADED = filled. Executed trades for an order: `tradebook_agent_tool` action `by_order`, same payload.

## Buy (entry)
`trading_agent_tool` action `place`, payload:
`{"transactionType": "BUY", "exchangeSegment": "NSE_EQ", "securityId": "<id>", "quantity": <qty>, "orderType": "LIMIT", "productType": "CNC", "price": <limit>, "validity": "DAY"}`
- The server runs a funds check first and blocks the order if cash is short. Don't pass `"preflight": false`.
- No tag field: the bot knows its orders by the order ids it saves.
- The reply has the order id. Save it.

Cancel: `trading_agent_tool` action `cancel`, payload `{"orderId": "<id>"}`.

## Market sell (exit)
`trading_agent_tool` action `place`: `"transactionType": "SELL"`, `"orderType": "MARKET"`, `"productType": "CNC"`, `"price": 0`, `"validity": "DAY"`, `quantity` = the bot's qty only.

## Stop at Dhan: conditional trigger (protection `single`, the level this profile uses)
Dhan's MCP has no Forever Order (GTT) tool, so the stop is a **conditional trigger** with a linked sell order. It stays at Dhan across days, like a single GTT: when the price falls to the stop, Dhan places the sell. The target is watched by the bot each run (`target_touched` → market sell, see order-executor). The plugin's author has used these triggers with real orders.

**Place** (`protect_place`): `alerts_agent_tool` action `create`, payload:
```
{"condition": {"comparisonType": "PRICE_WITH_VALUE", "exchangeSegment": "NSE_EQ", "securityId": "<id>",
               "operator": "LESS_THAN_EQUAL", "comparingValue": <stop>, "frequency": "ONCE",
               "expDate": "<today + 365 days, YYYY-MM-DD>", "userNote": "CLAUDEBOT stop <SYMBOL>"},
 "orders": [{"transactionType": "SELL", "exchangeSegment": "NSE_EQ", "productType": "CNC",
             "orderType": "MARKET", "securityId": "<id>", "quantity": <the bot's qty>,
             "validity": "DAY", "price": "0", "discQuantity": "0", "triggerPrice": "0"}]}
```
- The linked sell is MARKET, so a gap below the stop still exits (a limit below the stop could be skipped by the gap).
- If Dhan asks for a `timeFrame`, add `"timeFrame": "ONE_MIN"` to the condition. If it refuses the expiry date, use the latest date it allows and note it.
- The stop must be below the LTP. If the LTP is already at or below the stop, sell at MARKET instead.
- The reply has the trigger id (`alertId`, like `1100…_B_…`). Save it as the position's GTT id.
- A strategy with a target but no stop gets one trigger at the target instead (`GREATER_THAN_EQUAL`, comparingValue = target).
- **Only one trigger per position.** Never add a second trigger at the target: the two aren't linked (not OCO), so after one sells, the other could later sell shares you hold yourself.

**Move the stop** (`protect_modify`, trailing stop): action `modify`, the same payload as `create` with the new `comparingValue` plus `"alertId": "<id>"`. Keep the qty.
**Delete** (`protect_cancel`): action `delete`, payload `{"alertId": "<id>"}`. Delete it before any other exit, so it can't sell a second time.
**List** (`protect_list`): action `list` → each trigger's id, status (ACTIVE, or not when it has fired or expired), security and price. A fired trigger's sell shows in `orderbook_agent_tool` action `list`. The user's own triggers are in this list too: only touch ids the bot saved.
- Triggers expire on their `expDate`. Reconcile replaces an expired one.

## Day stop-loss order (fallback level `day_sl`)
Only if the user picks `day_sl` instead: `trading_agent_tool` action `place`:
`{"transactionType": "SELL", "exchangeSegment": "NSE_EQ", "securityId": "<id>", "quantity": <qty>, "orderType": "STOP_LOSS", "productType": "CNC", "triggerPrice": <stop>, "price": <stop x 0.98, tick-rounded down>, "validity": "DAY"}`. It lasts for the day only: place it again every run and cancel it before any other exit.

## Not used
- **Forever Orders (GTT / OCO):** Dhan has them in its app and API, but the MCP doesn't offer them (checked 2026-11-22: the trading tool's actions are place, modify, cancel and super orders only). If a later version of the MCP adds them, this profile can move to `two_leg`.
- **Super orders** (`super_place` …): they bundle a new entry with its target and stop, so they don't protect shares already held.

## Quirks
- Sells of delivery shares need DDPI on the Dhan account (or the daily eDIS / TPIN step in Dhan's app), or the sell is rejected.
- The quote tools need the Data API plan (above).
