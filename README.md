# AI Trader: an AI trading system for Claude Code

A Claude Code plugin that runs a trading system end to end through your broker:
- **any strategy:** ready-made ones, or your own described in plain words;
- **risk rules you choose** for each strategy;
- order placement with broker-side stop-losses;
- a journal, weekly reviews and a self-improvement loop.

Once a day you type one command and click your broker's login link. Every change the system wants to make to itself waits for your approval.

**Today:** 2 ready-made strategies plus your own, on NSE stocks, with one broker (Zerodha, through its official Kite MCP). More brokers are planned.

> **Disclaimer.** This is an experiment shared for education. It is **not investment advice**, and I am not a SEBI-registered adviser. Trading involves risk, including the loss of your capital. Past or backtested results don't guarantee future results. You are responsible for every order placed in your account.

## What it does
| Every trading day (`/trading-day`) | |
|---|---|
| Scan | Your active strategy's rules on official NSE end-of-day data (free, no API key) |
| Judgment | Claude can **veto** a signal (results due, bad news, upper circuit, data mismatch), never add one |
| Risk | The risk rules **you** chose for this strategy: risk per trade, max positions, weekly loss limit, drawdown halt, market filter, broker stop |
| Orders | LIMIT CNC buys tagged `CLAUDEBOT`, protected by **GTT orders at Zerodha** (stop and/or target, as the strategy defines) |
| Exits | The strategy's stop, target, trailing stop, exit rules or time exit |
| Journal | Every trade in detail, from signal to exit: why it was bought, plan vs fill, itemized charges, ₹ at risk, broker stop, best/worst price while open, net P&L and R. Plus every order and stop change with its time, every skipped signal with its numbers, and daily equity |
| Learning | Records every near-miss and its outcome. Suggests lessons and tweaks, which apply only if you approve |

## Strategies
Pick one when you set up, switch any time, or **describe your own in plain words, with any technical rule**: candles, gaps, breakouts, moving averages, RSI, volume, relative strength, "3 red days in a row", anything built from daily price and volume. Claude writes it as a small Python script on your computer, `~/ai-trader/strategies/<name>/strategy.py`. The same script runs every daily scan. Before you see it, every script is checked automatically:
- **Safety:** it can't open files, use the internet or run programs; it only calculates signals and never places orders itself.
- **No look-ahead:** the bot re-runs the script with later days hidden. If any past signal changes, the script used future data and is rejected.
- **Proof:** Claude reads the rules back in plain words and shows real recent examples where it fired, so you can check them on a chart.

*Technical rules only for now:* fundamental rules (P/E, market cap, EPS, ROE, debt, ...) aren't supported yet, and the bot will tell you if your strategy uses one.

| Strategy | Style | Rules (short) |
|---|---|---|
| **Momentum Breakout** | trend, weeks–months | Top-50 momentum NIFTY 500 stocks closing at a 52-week high on 1.5× volume, RSI 60–80; 14% stop, 1:2 target |
| **EMA 20/50 Crossover** | trend, weeks–months | NIFTY 500 stock above its 200-DMA where the 20-EMA crosses above the 50-EMA; sell on the cross back; 3×ATR stop |

Full rules, scripts and sources: [`strategies/library/`](plugins/ai-trader/skills/strategies/library/).

## You set every risk rule
Setup is a guided chat. You never edit files.
1. Pick a strategy, or describe your own, and see real recent examples of it firing.
2. Keep or change its stop-loss, target and sell rules.
3. Answer the risk questions: risk per trade, max positions, max position size, market filter, weekly loss limit, drawdown halt, broker stop. Each option is explained in rupees for your capital, and "No limit" is always a valid answer.
4. **Any more rules?** Add anything else, e.g. "max 2 stocks per sector" or "move my stop to breakeven after +5%".
5. Everything appears on one **review screen**: **Start**, **Change something** or **Cancel**.

The bot won't place a trade until every rule is answered, checked and confirmed. The one rule for every strategy: the bot never touches holdings outside its own positions.

## Install
You need [Claude Code](https://claude.com/claude-code), Python 3.8+ (no extra packages) and a Zerodha account.
```
/plugin marketplace add KaranRohra/ai-trader
/plugin install ai-trader@karan-rohra
```
Then run the setup once. It asks for your capital, walks you through choosing a strategy and its risk rules, and starts in **PAPER mode** (real data, simulated orders):
```
/ai-trader:trading-setup
```

**Updating from v1.0:** run `/plugin marketplace update karan-rohra`. Your settings carry over as pre-filled answers. On your next `/ai-trader:trading-day`, review them once and press Start; open positions keep their GTT stops meanwhile. The journal upgrades itself once (old files are backed up to `journal/backup_before_v2.1/`), and open positions become detailed trade rows.

## Daily use
Every trading day around **9:20–10:30 AM IST**:
```
/ai-trader:trading-day
```
Click the Kite login link when it appears. Zerodha requires a fresh login every day. Everything else runs on its own. Stops sit at Zerodha as GTT orders, so your computer doesn't need to stay on after the run.

Other skills:
- `strategies`: see, create, change or switch strategies and their risk rules.
- `trade-journal`: ask about any trade, e.g. "show my GLAND trade", for the full record and its timeline.
- `weekly-review`: weekend report.
- `strategy-lab`: runs monthly by itself.

## Safety notes
- **Use a separate Zerodha account**, or keep only the money you're giving the bot in it. The bot only touches its own positions, but the Kite connection can see the whole account.
- It starts in PAPER mode. LIVE is a deliberate switch in `trading-setup`.
- Risk rules change only when you change them. The self-improvement loop can suggest a change, but nothing applies until you press Apply.
- Your data stays on your computer in `~/ai-trader/`. Market data comes from NSE's public archives. Orders go through Zerodha's official hosted Kite MCP (`mcp.kite.trade`), under [Zerodha's privacy policy](https://zerodha.com/privacy-policy/).

## Files on your computer
```
~/ai-trader/
  config.json          capital, mode, active strategy
  strategies/          one folder per strategy: strategy.py (the rules) + strategy.json (settings and
                       the risk rules you confirmed); drafts/ while setting up
  positions.json       the bot's open positions (each remembers its strategy)
  journal/             trades.csv (one detailed row per trade), trade_events.csv (every order, GTT and
                       stop change), decisions.csv, daily.csv
  learning/            shadow_book.csv, lessons.md, pending.json, changelog.md, configs/ (every version)
  weekly/              weekly reports
  data/                cached NSE files
```

## License
MIT
