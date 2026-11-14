"""Technical-analysis helpers for AI Trader strategy scripts (Python standard library only).

A strategy script does `from aitrader_ta import *` and works on one stock's full daily history.
Every function takes lists of numbers and returns a list of the same length. Values that need more
history than exists are NaN (check with ok(x)). Nothing here ever looks at a later day than the one
it's computing.

Prices are split/bonus adjusted. Index i = one trading day of that stock (0 = oldest).
"""
import math

from engine import NAN, atr as _atr, ema as _ema, momentum as _momentum, ok, pct_change, prior_max, prior_mean, \
    prior_min, rsi as _rsi, sma as _sma, stdev as _stdev

__all__ = ["NAN", "ok", "sma", "ema", "rsi", "atr", "stdev", "highest", "lowest", "highest_before", "lowest_before",
           "avg_before", "ret", "momentum_score", "bb_upper", "bb_lower", "crossed_above", "crossed_below",
           "shift", "true_range", "body", "upper_wick", "lower_wick", "is_green", "is_red", "gap_pct",
           "rolling_max", "rolling_min", "count_true", "math"]


def sma(x, n):
    """Simple moving average of the last n values including today."""
    return list(_sma(x, n))


def ema(x, n):
    """Exponential moving average (seeded with the first n-value SMA)."""
    return list(_ema(x, n))


def rsi(close, n=14):
    """Wilder's RSI."""
    return list(_rsi(close, n))


def atr(high, low, close, n=14):
    """Wilder's average true range."""
    return list(_atr(high, low, close, n))


def stdev(x, n):
    """Population standard deviation of the last n values including today."""
    return list(_stdev(x, n))


def highest_before(x, n):
    """Highest of the PREVIOUS n values (excluding today) - e.g. a 52-week-high breakout: close[i] > highest_before(high, 252)[i]."""
    return list(prior_max(x, n))


def lowest_before(x, n):
    """Lowest of the previous n values (excluding today)."""
    return list(prior_min(x, n))


def avg_before(x, n):
    """Average of the previous n values (excluding today) - e.g. volume vs its average."""
    return list(prior_mean(x, n))


def rolling_max(x, n):
    """Highest of the last n values INCLUDING today."""
    out = [NAN] * len(x)
    for i in range(n - 1, len(x)):
        out[i] = max(x[i - n + 1:i + 1])
    return out


def rolling_min(x, n):
    """Lowest of the last n values including today."""
    out = [NAN] * len(x)
    for i in range(n - 1, len(x)):
        out[i] = min(x[i - n + 1:i + 1])
    return out


highest = rolling_max
lowest = rolling_min


def ret(x, n):
    """% change over the last n values."""
    return list(pct_change(x, n))


def momentum_score(close, short=126, long=252):
    """(0.5 x short return + 0.5 x long return) / 1-year volatility of daily log returns."""
    return list(_momentum(close, short, long))


def bb_upper(close, n=20, k=2.0):
    m, s = _sma(close, n), _stdev(close, n)
    return [a + k * b for a, b in zip(m, s)]


def bb_lower(close, n=20, k=2.0):
    m, s = _sma(close, n), _stdev(close, n)
    return [a - k * b for a, b in zip(m, s)]


def shift(x, n=1):
    """x shifted n days later: shift(close)[i] = close[i-1]."""
    return [NAN] * min(n, len(x)) + list(x[:-n]) if n > 0 else list(x)


def crossed_above(a, b):
    """True on the day a closes above b after being at or below it the day before."""
    return [i > 0 and ok(a[i]) and ok(b[i]) and ok(a[i - 1]) and ok(b[i - 1]) and a[i] > b[i] and a[i - 1] <= b[i - 1]
            for i in range(len(a))]


def crossed_below(a, b):
    return [i > 0 and ok(a[i]) and ok(b[i]) and ok(a[i - 1]) and ok(b[i - 1]) and a[i] < b[i] and a[i - 1] >= b[i - 1]
            for i in range(len(a))]


def true_range(high, low, close):
    return [high[0] - low[0]] + [max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
                                 for i in range(1, len(close))]


def body(open_, close):
    return [abs(c - o) for o, c in zip(open_, close)]


def upper_wick(open_, high, close):
    return [h - max(o, c) for o, h, c in zip(open_, high, close)]


def lower_wick(open_, low, close):
    return [min(o, c) - l for o, l, c in zip(open_, low, close)]


def is_green(open_, close):
    return [c > o for o, c in zip(open_, close)]


def is_red(open_, close):
    return [c < o for o, c in zip(open_, close)]


def gap_pct(open_, close):
    """Today's open vs yesterday's close, in %."""
    return [NAN] + [(open_[i] / close[i - 1] - 1) * 100 if close[i - 1] else NAN for i in range(1, len(close))]


def count_true(flags, n):
    """How many of the last n days (incl. today) were True - e.g. '3 green days in a row' = count_true(green, 3)[i] == 3."""
    out, run = [0] * len(flags), 0
    for i, f in enumerate(flags):
        run += 1 if f else 0
        if i >= n:
            run -= 1 if flags[i - n] else 0
        out[i] = run
    return out
