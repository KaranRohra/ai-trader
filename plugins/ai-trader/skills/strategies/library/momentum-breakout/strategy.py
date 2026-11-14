"""Momentum Breakout - buy rules (AI Trader strategy script).

The bot calls prepare() once per stock with its full daily history and uses the answers for both the
daily scan and the backtest. Ranking (top 50 by momentum score) is done by the bot across all stocks
using 'score' and max_rank. Stop (14%) and target (1:2) are in strategy.json.
"""
from aitrader_ta import *


def prepare(stock, market, p):
    c, h, v = stock.close, stock.high, stock.volume
    n = len(c)
    sma50, sma200 = sma(c, 50), sma(c, 200)
    high52 = highest_before(h, 252)          # highest high of the previous 252 sessions
    vol_avg = avg_before(v, 50)              # average volume of the previous 50 sessions
    rsi14 = rsi(c, 14)
    ema20 = ema(c, 20)
    return {
        "score": momentum_score(c, 126, 252),
        "checks": {
            "trend": [c[i] > sma50[i] > sma200[i] for i in range(n)],
            "breakout_52w": [c[i] > high52[i] for i in range(n)],
            "volume": [v[i] >= p["vol_mult"] * vol_avg[i] for i in range(n)],
            "rsi": [p["rsi_min"] <= rsi14[i] <= p["rsi_max"] for i in range(n)],
            "not_extended": [(c[i] / ema20[i] - 1) * 100 <= p["max_ext_pct"] for i in range(n)],
        },
    }
