# Dhan (DhanHQ MCP): exact tool use

**Ready-made, not tested with real orders yet.** The tool names and payloads below were read from the Dhan MCP's own tool list on 2026-11-22, and the read-only tools were called on a real account. The order tools have not been called. The `order-executor` skill says *what* to do; this file says *how* on Dhan.

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

## Day stop-loss order (protection `day_sl`, the level this profile uses)
`trading_agent_tool` action `place`:
`{"transactionType": "SELL", "exchangeSegment": "NSE_EQ", "securityId": "<id>", "quantity": <qty>, "orderType": "STOP_LOSS", "productType": "CNC", "triggerPrice": <stop>, "price": <stop x 0.98, tick-rounded down>, "validity": "DAY"}`
- It lasts for the day only: place it again every run (Reconcile), and cancel it (`cancel` with its order id) before any other exit or a stop change.
- If the LTP is already at or below the stop, sell at MARKET instead.

## Not used (yet)
- **Conditional triggers** (`alerts_agent_tool`, actions `create` / `modify` / `list` / `delete`): a price condition (`PRICE_WITH_VALUE`, `LESS_THAN` the stop, `expDate`) with a linked SELL CNC order stays at Dhan across days, like a single GTT. It could raise this profile to `single`, but it hasn't been tested with a real order, so the bot doesn't use it. `list` shows the user's own triggers: never change or delete them.
- **Super orders** (`super_place` …): they bundle a new entry with its target and stop, so they don't protect shares already held. Not used.

## Quirks
- Sells of delivery shares need DDPI on the Dhan account (or the daily eDIS / TPIN step in Dhan's app), or the sell is rejected.
- The quote tools need the Data API plan (above).
