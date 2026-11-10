# AI Momentum Trader: a Claude Code trading bot for Zerodha

A set of Claude Code **skills** that run a rules-based momentum strategy on the NIFTY 500 through your Zerodha account. Once a day you type one command and click the Kite login link. The bot then scans, manages exits and stops, places new trades, keeps a journal, and learns from its results. Every change it wants to make waits for your approval.

> **Disclaimer.** This is an experiment shared for education. It is **not investment advice**, and I am not a SEBI-registered adviser. Trading involves risk, including the loss of your capital. Past or backtested results don't guarantee future results. You are responsible for every order placed in your account.

## What it does
| Every trading day (`/trading-day`) | |
|---|---|
| Scan | NIFTY 500 momentum breakouts on official NSE end-of-day data (free, no API key) |
| Judgment | Claude can **veto** a signal (results due, bad news, upper circuit, data mismatch), never add one |
| Risk | 2% risk per trade, fixed 14% stop, max 10 positions (cash usually limits to ~7), weekly loss limit −8% (no new entries for the rest of the week), halt new entries at −30% from the highest equity reached |
| Orders | LIMIT CNC buys tagged `CLAUDEBOT`, each protected by a **two-leg GTT at Zerodha** (stop + target) |
| Exits | Stop-loss or a fixed **1:2 target**, whichever comes first |
| Journal | Every decision with its reason, P&L, R-multiples, daily equity |
| Learning | Records every near-miss and its outcome. Suggests lessons and tweaks, which apply only if you approve |

**Strategy (v1.0):**
- Regime: NIFTY 500 above its 200-DMA.
- Trend: close > 50-DMA > 200-DMA.
- Momentum score (6- and 12-month return ÷ volatility) in the top 50.
- Close at a new **52-week high**, on volume ≥ 1.5× average.
- RSI 60–80, and no more than 10% above the 20-EMA.
- Exits: fixed 14% stop-loss, fixed 1:2 target (+28%), as one two-leg GTT.

**Backtest (Jan 2016 – Nov 2026, ₹1L, Zerodha costs included, idle cash earns nothing):**
- 19.5% CAGR, −20.4% max drawdown, 250 trades.
- NIFTY 500 price index: 11.6% CAGR, −38.3% max drawdown.
- Nearby settings (13–16% stop, 1:2 target) gave 16.6–20.1% CAGR.
- Win rate 54%. Losing years: 2018 −18.2%, 2022 −10.5%, 2025 −9.7%. The worst month was −13.9% (March 2026).
- A backtest is not a forecast.

Full rules: [`momentum-scan/SKILL.md`](plugins/ai-momentum-trader/skills/momentum-scan/SKILL.md).

## Install
You need [Claude Code](https://claude.com/claude-code), Python 3.8+ (no extra packages) and a Zerodha account.
```
/plugin marketplace add KaranRohra/ai-momentum-trader
/plugin install ai-momentum-trader@karan-rohra
```
Then run the setup once. It asks for your capital and starts in **PAPER mode** (real data, simulated orders):
```
/ai-momentum-trader:trading-setup
```

## Daily use
Every trading day around **9:20–10:30 AM IST**:
```
/ai-momentum-trader:trading-day
```
Click the Kite login link when it appears. Zerodha requires a fresh login every day. Everything else runs on its own. Stops sit at Zerodha as GTT orders, so your computer doesn't need to stay on after the run.

Other skills: `weekly-review` (weekend report) and `strategy-lab` (runs monthly by itself).

## Safety notes
- **Use a separate Zerodha account**, or keep only the money you're giving the bot in it. The bot only touches its own positions, but the Kite connection can see the whole account.
- It starts in PAPER mode. LIVE is a deliberate switch in `trading-setup`.
- Risk rules are locked. The self-improvement loop can't change them.
- Your data stays on your computer in `~/ai-momentum-trader/`. Market data comes from NSE's public archives. Orders go through Zerodha's official hosted Kite MCP (`mcp.kite.trade`), under [Zerodha's privacy policy](https://zerodha.com/privacy-policy/).

## Files on your computer
```
~/ai-momentum-trader/
  config.json          capital, mode, strategy version
  positions.json       the bot's open positions
  journal/             trades.csv, decisions.csv, daily.csv
  learning/            shadow_book.csv, lessons.md, pending.json, changelog.md, configs/
  weekly/              weekly reports
  data/                cached NSE files
```

## License
MIT
