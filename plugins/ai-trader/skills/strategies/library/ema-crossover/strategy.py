"""EMA 20/50 Crossover - buy and sell rules (AI Trader strategy script).

Buy: the fast EMA crosses above the slow EMA today, and the close is above the trend SMA.
Sell (next morning): the fast EMA closes below the slow EMA. The 3 x ATR stop is in strategy.json.
Signals are taken best 6-month return first.
"""
from aitrader_ta import *


def prepare(stock, market, p):
    c = stock.close
    n = len(c)
    fast, slow = ema(c, int(p["fast"])), ema(c, int(p["slow"]))
    trend = sma(c, int(p["trend"]))
    return {
        "score": ret(c, 126),
        "checks": {
            "cross_up": crossed_above(fast, slow),
            "uptrend": [c[i] > trend[i] for i in range(n)],
        },
        "sell": ["cross_down" if fast[i] < slow[i] else False for i in range(n)],
    }
