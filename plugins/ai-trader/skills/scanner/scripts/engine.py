#!/usr/bin/env python3
"""Strategy engine for the AI Trader (Python standard library only).

- Strategies: a folder with strategy.json (universe, parameters, stop / target / trailing stop, execution,
  the user's risk answers, and plain-words descriptions) and strategy.py (the buy/sell logic, any technical
  rule, written for the user by Claude). One runner uses the script for both the daily scan and the backtest.
- Guardrails for scripts: a static check (no files, network or other programs), and a look-ahead test
  (answers for past days must not change when later days are added).
- A small rule language for the market filter (e.g. 'nifty500 > nifty500_sma(200)') and shadow-book features.
- The user's settings in ~/ai-trader/config.json, including the one-time move from v1.0.

Nothing in this file decides a risk number. Ready-made strategies only carry *suggested* risk settings;
the bot trades a strategy only after the user has confirmed its risk block on the review screen.
"""
import datetime as dt
import json
import math
import os
import re
import shutil
from array import array
from collections import deque
from pathlib import Path

HOME = Path(os.environ.get("AI_TRADER_HOME", Path.home() / "ai-trader"))
CONFIG = HOME / "config.json"
USER_STRATEGIES = HOME / "strategies"
SKILLS = Path(__file__).resolve().parents[2]
LIBRARY = SKILLS / "strategies" / "library"
LEGACY_ID = "momentum-breakout"   # positions/journal rows from v1.0 have no strategy field
NAN = float("nan")
DEFAULT_PRIORITY = "sma(20, turnover_cr)"  # signal order when a strategy has no rank_by: most liquid first

UNIVERSES = {  # name -> (NSE constituents file, backtest proxy = ranks by 6-month traded value)
    "nifty50": ("ind_nifty50list.csv", (1, 50)),
    "nifty100": ("ind_nifty100list.csv", (1, 100)),
    "nifty200": ("ind_nifty200list.csv", (1, 200)),
    "nifty500": ("ind_nifty500list.csv", (1, 500)),
    "midcap150": ("ind_niftymidcap150list.csv", (101, 250)),
    "smallcap250": ("ind_niftysmallcap250list.csv", (251, 500)),
}

# The risk block: every key must be answered by the user before the bot trades the strategy.
RISK_FIELDS = {
    "sizing": "how big each position is: {'type': 'risk', 'pct': R} risks R% of equity between entry and stop; "
              "{'type': 'fixed_pct', 'pct': P} puts P% of equity in each position",
    "max_positions": "most open positions at once (null = no limit; cash still limits it)",
    "max_position_pct": "largest single position as % of equity (null = no cap)",
    "weekly_loss_limit_pct": "no new entries for the rest of the week after a week's loss of this % (null = no weekly limit)",
    "halt_drawdown_pct": "no new entries while equity is this % below its highest level (null = no halt)",
    "regime": "market filter: a rule on the NIFTY 500 index, e.g. 'nifty500 > nifty500_sma(200)'; "
              "false that day -> no new entries (null = no market filter)",
    "broker_stop_required": "true = every position must have a stop order (GTT) at the broker; if it can't be placed, the position is sold",
    "stops_only_up": "true = a stop can be raised but never lowered",
}

# ---------------------------------------------------------------- indicators (full arrays, NaN = not enough history)


def _nan(n):
    return array("d", [NAN]) * n


def ok(x):
    return x == x  # False for NaN


def _first(x):
    for i, v in enumerate(x):
        if v == v:
            return i
    return len(x)


def sma(x, n):
    out, s, st = _nan(len(x)), 0.0, _first(x)
    for i in range(st, len(x)):
        s += x[i]
        if i - st >= n:
            s -= x[i - n]
        if i - st >= n - 1:
            out[i] = s / n
    return out


def ema(x, n):
    out, a, e, st = _nan(len(x)), 2.0 / (n + 1), 0.0, _first(x)
    for i in range(st, len(x)):
        if i - st == n - 1:
            e = sum(x[st:st + n]) / n
            out[i] = e
        elif i - st >= n:
            e = e + a * (x[i] - e)
            out[i] = e
    return out


def stdev(x, n):
    out, s, s2 = _nan(len(x)), 0.0, 0.0
    for i, v in enumerate(x):
        s += v
        s2 += v * v
        if i >= n:
            s -= x[i - n]
            s2 -= x[i - n] ** 2
        if i >= n - 1:
            m = s / n
            out[i] = math.sqrt(max(s2 / n - m * m, 0.0))
    return out


def rsi(c, n=14):
    out = _nan(len(c))
    if len(c) <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        g += max(d, 0)
        l += max(-d, 0)
    g, l = g / n, l / n
    out[n] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def atr(h, l, c, n=14):
    out = _nan(len(c))
    if len(c) <= n:
        return out
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    a = sum(tr[1:n + 1]) / n
    out[n] = a
    for i in range(n + 1, len(c)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def _prior_extreme(x, n, better):
    """max/min of the previous n values (excluding today)."""
    out, q = _nan(len(x)), deque()
    for i in range(len(x)):
        while q and q[0] < i - n:
            q.popleft()
        if i >= n:
            out[i] = x[q[0]]
        while q and better(x[i], x[q[-1]]):
            q.pop()
        q.append(i)
    return out


def prior_max(x, n):
    return _prior_extreme(x, n, lambda a, b: a >= b)


def prior_min(x, n):
    return _prior_extreme(x, n, lambda a, b: a <= b)


def prior_mean(x, n):
    out, s = _nan(len(x)), 0.0
    for i in range(len(x)):
        if i >= n:
            out[i] = s / n
            s -= x[i - n]
        s += x[i]
    return out


def pct_change(x, n):
    out = _nan(len(x))
    for i in range(n, len(x)):
        if x[i - n] and ok(x[i - n]) and ok(x[i]):
            out[i] = (x[i] / x[i - n] - 1) * 100
    return out


def momentum(c, ns, nl, ws=0.5, wl=0.5):
    """(ws x short return + wl x long return) / annualized volatility of daily log returns over the long window."""
    out = _nan(len(c))
    lr = array("d", [0.0]) * len(c)
    for i in range(1, len(c)):
        if c[i - 1] > 0 and c[i] > 0:
            lr[i] = math.log(c[i] / c[i - 1])
    s = s2 = 0.0
    for i in range(len(c)):
        s += lr[i]
        s2 += lr[i] * lr[i]
        if i > nl:
            s -= lr[i - nl]
            s2 -= lr[i - nl] ** 2
        if i >= nl:
            mean = s / nl
            var = max(s2 / nl - mean * mean, 1e-12)
            vol = math.sqrt(var * 252)
            out[i] = (ws * (c[i] / c[i - ns] - 1) + wl * (c[i] / c[i - nl] - 1)) / vol
    return out

# ---------------------------------------------------------------- the rule language

FIELDS = {"open": "o", "high": "h", "low": "l", "close": "c", "volume": "v", "turnover": "t", "turnover_cr": "t_cr"}
VARS = {"rank", "nifty500"}
# name -> (min args, max args, arg kinds: "n" = number or parameter name, "f" = price field, "e" = any expression)
FUNCS = {
    "sma": (1, 2, "nf"), "ema": (1, 2, "nf"), "rsi": (1, 1, "n"), "atr": (1, 1, "n"),
    "highest_high": (1, 1, "n"), "lowest_low": (1, 1, "n"), "highest_close": (1, 1, "n"), "lowest_close": (1, 1, "n"),
    "avg_volume": (1, 1, "n"), "ret": (1, 1, "n"), "momentum_score": (2, 2, "nn"),
    "bb_upper": (2, 2, "nn"), "bb_lower": (2, 2, "nn"),
    "nifty500_sma": (1, 1, "n"), "nifty500_ema": (1, 1, "n"), "nifty500_ret": (1, 1, "n"),
    "prev": (1, 2, "en"), "min": (2, 2, "ee"), "max": (2, 2, "ee"), "abs": (1, 1, "e"),
}
VOCABULARY = {
    "fields": "open, high, low, close, volume, turnover (₹), turnover_cr (₹ crore) - today's values",
    "rank": "rank among the universe by the strategy's rank_by (1 = best); needs rank_by",
    "nifty500": "NIFTY 500 index close",
    "sma(n[, field])": "simple moving average of the last n sessions incl. today (field defaults to close)",
    "ema(n[, field])": "exponential moving average",
    "rsi(n)": "RSI (Wilder)", "atr(n)": "average true range (Wilder)",
    "highest_high(n) / lowest_low(n)": "highest high / lowest low of the PREVIOUS n sessions (excl. today)",
    "highest_close(n) / lowest_close(n)": "highest / lowest close of the previous n sessions (excl. today)",
    "avg_volume(n)": "average volume of the previous n sessions (excl. today)",
    "ret(n)": "% return over the last n sessions",
    "momentum_score(short, long)": "(0.5 x short return + 0.5 x long return) / 1-year volatility",
    "bb_upper(n, k) / bb_lower(n, k)": "Bollinger bands: sma(n) +/- k standard deviations",
    "nifty500_sma(n) / nifty500_ema(n) / nifty500_ret(n)": "the same, on the NIFTY 500 index",
    "prev(x[, n])": "value of any expression n sessions ago (default 1) - e.g. crossovers",
    "min(a, b) / max(a, b) / abs(x)": "",
    "operators": "+ - * /  > >= < <= == !=  and or not  x between a and b  ( )",
    "parameters": "any name from the strategy's params, e.g. rsi(14) <= rsi_max",
}

_TOK = re.compile(r"\s*(?:(\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|\.\d+)|([A-Za-z_][A-Za-z0-9_]*)|(>=|<=|==|!=|[-+*/(),<>]))")
_KEYWORDS = {"and", "or", "not", "between"}


class RuleError(ValueError):
    pass


def _tokens(text):
    pos, out = 0, []
    text = text.strip()
    while pos < len(text):
        m = _TOK.match(text, pos)
        if not m or m.end() == pos:
            raise RuleError(f"can't read '{text[pos:pos + 12]}' in: {text}")
        num, name, op = m.groups()
        out.append(("num", float(num)) if num else ("name", name.lower()) if name else ("op", op))
        pos = m.end()
    return out


class _Parser:
    def __init__(self, text):
        self.text, self.t, self.i = text, _tokens(text), 0

    def peek(self, kind=None, val=None):
        if self.i >= len(self.t):
            return False
        k, v = self.t[self.i]
        return (kind is None or k == kind) and (val is None or v == val)

    def take(self, kind=None, val=None):
        if not self.peek(kind, val):
            got = self.t[self.i][1] if self.i < len(self.t) else "end of rule"
            raise RuleError(f"expected {val or kind} but found '{got}' in: {self.text}")
        self.i += 1
        return self.t[self.i - 1][1]

    def parse(self):
        node = self.orx()
        if self.i != len(self.t):
            raise RuleError(f"unexpected '{self.t[self.i][1]}' in: {self.text}")
        return node

    def orx(self):
        n = self.andx()
        while self.peek("name", "or"):
            self.i += 1
            n = ("or", n, self.andx())
        return n

    def andx(self):
        n = self.notx()
        while self.peek("name", "and"):
            self.i += 1
            n = ("and", n, self.notx())
        return n

    def notx(self):
        if self.peek("name", "not"):
            self.i += 1
            return ("not", self.notx())
        return self.cmp()

    def cmp(self):
        a = self.add()
        if self.peek("op") and self.t[self.i][1] in (">", ">=", "<", "<=", "==", "!="):
            op = self.take()
            return ("cmp", op, a, self.add())
        if self.peek("name", "between"):
            self.i += 1
            lo = self.add()
            self.take("name", "and")
            return ("between", a, lo, self.add())
        return a

    def add(self):
        n = self.mul()
        while self.peek("op") and self.t[self.i][1] in "+-":
            n = ("bin", self.take(), n, self.mul())
        return n

    def mul(self):
        n = self.unary()
        while self.peek("op") and self.t[self.i][1] in "*/":
            n = ("bin", self.take(), n, self.unary())
        return n

    def unary(self):
        if self.peek("op", "-"):
            self.i += 1
            return ("neg", self.unary())
        if self.peek("op", "+"):
            self.i += 1
        return self.atom()

    def atom(self):
        if self.peek("num"):
            return ("num", self.take())
        if self.peek("op", "("):
            self.i += 1
            n = self.orx()
            self.take("op", ")")
            return n
        name = self.take("name")
        if name in _KEYWORDS:
            raise RuleError(f"'{name}' in the wrong place in: {self.text}")
        if self.peek("op", "("):
            self.i += 1
            args = []
            if not self.peek("op", ")"):
                args.append(self.orx())
                while self.peek("op", ","):
                    self.i += 1
                    args.append(self.orx())
            self.take("op", ")")
            return ("call", name, args)
        return ("var", name)


def parse(text):
    if not isinstance(text, str) or not text.strip():
        raise RuleError("empty rule")
    return _Parser(text).parse()


def check(node, params, has_rank=True, index_only=False, text=""):
    """Raise RuleError for unknown names, wrong argument counts or non-constant indicator lengths."""
    kind = node[0]
    if kind == "num":
        return
    if kind == "var":
        name = node[1]
        if index_only and name != "nifty500" and name not in params:
            raise RuleError(f"the market filter can only use the NIFTY 500 (nifty500, nifty500_sma(n), ...): '{name}' in: {text}")
        if name == "rank" and not has_rank:
            raise RuleError(f"'rank' needs a rank_by in the strategy: {text}")
        if name not in FIELDS and name not in VARS and name not in params:
            raise RuleError(f"unknown name '{name}' in: {text}. Use a price field, an indicator or a parameter")
        return
    if kind == "call":
        name, args = node[1], node[2]
        if name not in FUNCS:
            raise RuleError(f"unknown function '{name}(...)' in: {text}. Known: {', '.join(sorted(FUNCS))}")
        if index_only and not name.startswith("nifty500_") and name not in ("min", "max", "abs", "prev"):
            raise RuleError(f"the market filter can only use NIFTY 500 functions: '{name}' in: {text}")
        lo, hi, kinds = FUNCS[name]
        if not lo <= len(args) <= hi:
            raise RuleError(f"{name}() takes {lo}{'-' + str(hi) if hi != lo else ''} argument(s) in: {text}")
        for a, k in zip(args, kinds):
            if k == "n" and not (a[0] == "num" or (a[0] == "var" and a[1] in params)):
                raise RuleError(f"{name}(): lengths must be a number or a parameter name in: {text}")
            if k == "f" and not (a[0] == "var" and a[1] in FIELDS):
                raise RuleError(f"{name}(): the 2nd argument must be a price field ({', '.join(FIELDS)}) in: {text}")
            if k == "e":
                check(a, params, has_rank, index_only, text)
        return
    for child in node[1:]:
        if isinstance(child, tuple):
            check(child, params, has_rank, index_only, text)


def lookback(node, params):
    """Sessions of history a rule needs (for how much data to load)."""
    if not isinstance(node, tuple):
        return 0
    if node[0] == "call":
        nums = [params.get(a[1], 0) if a[0] == "var" else a[1] for a in node[2] if a[0] in ("num", "var")]
        own = max([float(x) for x in nums if isinstance(x, (int, float))] + [0])
        if node[1] == "prev":
            return lookback(node[2][0], params) + (own if len(node[2]) > 1 else 1)
        return own + 1 + max([lookback(a, params) for a in node[2]] + [0])
    return max([lookback(c, params) for c in node[1:] if isinstance(c, tuple)] + [0])


# ---------------------------------------------------------------- vector evaluation


def _is_vec(x):
    return isinstance(x, array)


def _apply(f, a, b):
    if not _is_vec(a) and not _is_vec(b):
        return f(a, b)
    n = len(a) if _is_vec(a) else len(b)
    A = a if _is_vec(a) else [a] * n
    B = b if _is_vec(b) else [b] * n
    return array("d", map(f, A, B))


def _apply1(f, a):
    return array("d", map(f, a)) if _is_vec(a) else f(a)


def _div(x, y):
    return x / y if y else NAN


_ARITH = {"+": lambda x, y: x + y, "-": lambda x, y: x - y, "*": lambda x, y: x * y, "/": _div}
_CMP = {">": lambda x, y: x > y, ">=": lambda x, y: x >= y, "<": lambda x, y: x < y, "<=": lambda x, y: x <= y,
        "==": lambda x, y: x == y, "!=": lambda x, y: x != y}


def _cmpf(op):
    f = _CMP[op]
    return lambda x, y: NAN if (x != x or y != y) else (1.0 if f(x, y) else 0.0)


def _and(x, y):
    if x == 0.0 or y == 0.0:
        return 0.0
    return NAN if (x != x or y != y) else 1.0


def _or(x, y):
    if (x == x and x != 0.0) or (y == y and y != 0.0):
        return 1.0
    return NAN if (x != x or y != y) else 0.0


def _not(x):
    return NAN if x != x else (0.0 if x else 1.0)


class IndexData:
    """NIFTY 500 closes per panel date (missing days carried forward) and cached indicators on them."""

    def __init__(self, values):
        vals, last = array("d"), NAN
        for v in values:
            last = float(v) if v else last
            vals.append(last)
        self.close, self.cache = vals, {}

    def get(self, name, n):
        key = (name, n)
        if key not in self.cache:
            f = {"nifty500_sma": sma, "nifty500_ema": ema, "nifty500_ret": pct_change}[name]
            self.cache[key] = f(self.close, n)
        return self.cache[key]


class Ctx:
    """Evaluates rules over one price series (or, with series=None, over the index for the market filter)."""

    def __init__(self, series, params, index=None, rank=None):
        self.s, self.params, self.index, self.rank, self.cache = series, params, index, rank, {}
        self.n = len(series.c) if series is not None else len(index.close)

    def _num(self, a):
        return float(a[1]) if a[0] == "num" else float(self.params[a[1]])

    def _align(self, full):
        if self.s is None:
            return full
        return array("d", (full[k] for k in self.s.di))

    def field(self, name):
        if name == "t_cr":
            if "t_cr" not in self.cache:
                self.cache["t_cr"] = array("d", (t / 1e7 for t in self.s.t))
            return self.cache["t_cr"]
        return getattr(self.s, name)

    def ev(self, node):
        k = node[0]
        if k == "num":
            return float(node[1])
        if k == "var":
            name = node[1]
            if name in self.params:
                return float(self.params[name])
            if name == "nifty500":
                return self._align(self.index.close)
            if name == "rank":
                if self.rank is None:
                    raise RuleError("rank is not available here")
                return self.rank
            return self.field(FIELDS[name])
        if k == "bin":
            return _apply(_ARITH[node[1]], self.ev(node[2]), self.ev(node[3]))
        if k == "neg":
            return _apply1(lambda x: -x, self.ev(node[1]))
        if k == "cmp":
            return _apply(_cmpf(node[1]), self.ev(node[2]), self.ev(node[3]))
        if k == "between":
            x = self.ev(node[1])
            return _apply(_and, _apply(_cmpf(">="), x, self.ev(node[2])), _apply(_cmpf("<="), x, self.ev(node[3])))
        if k == "and":
            return _apply(_and, self.ev(node[1]), self.ev(node[2]))
        if k == "or":
            return _apply(_or, self.ev(node[1]), self.ev(node[2]))
        if k == "not":
            return _apply1(_not, self.ev(node[1]))
        if k == "call":
            return self.call(node[1], node[2])
        raise RuleError(f"bad node {node}")

    def call(self, name, args):
        if name == "prev":
            x = self.ev(args[0])
            n = int(self._num(args[1])) if len(args) > 1 else 1
            if not _is_vec(x) or n <= 0:
                return x
            return (_nan(min(n, len(x))) + x[:-n]) if n < len(x) else _nan(len(x))
        if name in ("min", "max"):
            f = min if name == "min" else max
            return _apply(lambda a, b: NAN if (a != a or b != b) else f(a, b), self.ev(args[0]), self.ev(args[1]))
        if name == "abs":
            return _apply1(abs, self.ev(args[0]))
        nums = tuple(self._num(a) for a in args if a[0] != "var" or a[1] not in FIELDS)
        fld = next((FIELDS[a[1]] for a in args if a[0] == "var" and a[1] in FIELDS), "c")
        key = (name, nums, fld)
        if key in self.cache:
            return self.cache[key]
        if name.startswith("nifty500_"):
            val = self._align(self.index.get(name, int(nums[0])))
        else:
            s, n = self.s, int(nums[0])
            if name == "sma":
                val = sma(self.field(fld), n)
            elif name == "ema":
                val = ema(self.field(fld), n)
            elif name == "rsi":
                val = rsi(s.c, n)
            elif name == "atr":
                val = atr(s.h, s.l, s.c, n)
            elif name == "highest_high":
                val = prior_max(s.h, n)
            elif name == "lowest_low":
                val = prior_min(s.l, n)
            elif name == "highest_close":
                val = prior_max(s.c, n)
            elif name == "lowest_close":
                val = prior_min(s.c, n)
            elif name == "avg_volume":
                val = prior_mean(s.v, n)
            elif name == "ret":
                val = pct_change(s.c, n)
            elif name == "momentum_score":
                val = momentum(s.c, int(nums[0]), int(nums[1]))
            elif name in ("bb_upper", "bb_lower"):
                m, sd, k = sma(s.c, n), stdev(s.c, n), nums[1]
                sign = 1 if name == "bb_upper" else -1
                val = array("d", (a + sign * k * b for a, b in zip(m, sd)))
            else:
                raise RuleError(f"unknown function {name}")
        self.cache[key] = val
        return val

    def vec(self, node):
        """Always an array (constants are broadcast)."""
        v = self.ev(node)
        return v if _is_vec(v) else array("d", [v]) * self.n

# ---------------------------------------------------------------- strategies: a settings file + a logic script


class StrategyError(ValueError):
    pass


def _num_or_param(v, params, what):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str) and v in params:
        return float(params[v])
    raise StrategyError(f"{what}: expected a number or a parameter name, got {v!r}")


def _today():
    return dt.date.today().isoformat()


SETTINGS_FILE, SCRIPT_FILE = "strategy.json", "strategy.py"
ALLOWED_IMPORTS = {"aitrader_ta", "math", "statistics", "datetime", "itertools", "functools", "collections", "bisect"}
BANNED_CALLS = {"open", "eval", "exec", "compile", "__import__", "input", "globals", "locals", "vars", "breakpoint",
                "getattr", "setattr", "delattr", "exit", "quit", "help", "memoryview"}
OUTPUT_LISTS = ("buy", "score", "sell", "stop_level", "trail_level")


def check_script(text, extra_imports=()):
    """Static safety check of a strategy script. Returns a list of problems in plain words (empty = fine).
    Scripts may only compute: no files, no network, no other programs, no hidden attributes."""
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [f"the script has a syntax error on line {e.lineno}: {e.msg}"]
    problems, allowed = [], ALLOWED_IMPORTS | set(extra_imports)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in allowed:
                    problems.append(f"line {node.lineno}: imports '{a.name}' (allowed: {', '.join(sorted(allowed))})")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in allowed:
                problems.append(f"line {node.lineno}: imports from '{node.module}' (allowed: {', '.join(sorted(allowed))})")
        elif isinstance(node, ast.Name) and node.id in BANNED_CALLS:
            problems.append(f"line {node.lineno}: uses '{node.id}', which strategy scripts may not use")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            problems.append(f"line {node.lineno}: uses the hidden attribute '{node.attr}'")
    if not any(isinstance(n, ast.FunctionDef) and n.name == "prepare" for n in tree.body):
        problems.append("the script must define prepare(stock, market, p)")
    return problems


class Bars:
    """One stock's daily history as the script sees it: .date .open .high .low .close .volume .turnover
    (.turnover in ₹; prices split/bonus adjusted), .symbol. Index 0 = oldest day."""
    __slots__ = ("symbol", "date", "open", "high", "low", "close", "volume", "turnover")

    def __init__(self, symbol, date, o, h, l, c, v, t):
        self.symbol, self.date = symbol, date
        self.open, self.high, self.low, self.close, self.volume, self.turnover = o, h, l, c, v, t

    @classmethod
    def from_series(cls, s, dates, upto=None):
        n = len(s.c) if upto is None else upto + 1
        return cls(s.sym, [dates[k] for k in s.di[:n]], s.o[:n], s.h[:n], s.l[:n], s.c[:n], s.v[:n], s.t[:n])

    def __len__(self):
        return len(self.close)


class Market:
    """What a script may know about the rest of the market, up to the stock's last day:
    .nifty500 (NIFTY 500 close on each of the stock's days) and .stock(symbol) (another stock's Bars)."""

    def __init__(self, panel, index, bars):
        self._panel, self._last = panel, bars.date[-1] if len(bars) else None
        pos = {d: k for k, d in enumerate(panel.dates)}
        self.nifty500 = [index.close[pos[d]] if d in pos else NAN for d in bars.date]

    def stock(self, symbol):
        s = self._panel.by_sym.get(symbol)
        if s is None:
            return None
        n = sum(1 for k in s.di if self._panel.dates[k] <= self._last)
        return Bars.from_series(s, self._panel.dates, n - 1) if n else None


class Result:
    """A script's answer for one stock, one value per day."""
    __slots__ = ("buy", "checks", "score", "sell", "stop_level", "trail_level", "atr_cache", "s", "rank", "rank_score")

    def __init__(self, s, out, n, sid):
        self.s, self.atr_cache, self.rank, self.rank_score = s, {}, None, None
        if not isinstance(out, dict):
            raise StrategyError(f"{sid}: prepare() must return a dict (got {type(out).__name__})")
        checks = out.get("checks") or {}
        for name, arr in checks.items():
            if len(arr) != n:
                raise StrategyError(f"{sid}: check '{name}' has {len(arr)} values for {n} days")
        self.checks = {name: [bool(x) and x == x for x in arr] for name, arr in checks.items()}
        if out.get("buy") is not None:
            self.buy = [bool(x) and x == x for x in out["buy"]]
        elif self.checks:
            self.buy = [all(col) for col in zip(*self.checks.values())]
        else:
            raise StrategyError(f"{sid}: prepare() must return 'buy' or 'checks'")
        for k in OUTPUT_LISTS:
            v = getattr(self, k, None) if k == "buy" else out.get(k)
            if v is not None and len(v) != n:
                raise StrategyError(f"{sid}: '{k}' has {len(v)} values for {n} days")
        self.score = [float(x) if isinstance(x, (int, float)) and x == x else NAN for x in out["score"]] \
            if out.get("score") is not None else None
        # sell: True / False per day, or a short reason text (e.g. "cross_down") that the journal records
        self.sell = [(x if isinstance(x, str) else bool(x) and x == x) or False for x in out["sell"]] \
            if out.get("sell") is not None else None
        self.stop_level = list(out["stop_level"]) if out.get("stop_level") is not None else None
        self.trail_level = list(out["trail_level"]) if out.get("trail_level") is not None else None

    def atr(self, period):
        if period not in self.atr_cache:
            self.atr_cache[period] = atr(self.s.h, self.s.l, self.s.c, period)
        return self.atr_cache[period]


def _load_module(path, text):
    import importlib.util
    import sys as _sys
    here = str(Path(__file__).resolve().parent)
    if here not in _sys.path:
        _sys.path.insert(0, here)  # for `from aitrader_ta import *`
    name = "aitrader_strategy_" + re.sub(r"\W", "_", str(path))
    spec = importlib.util.spec_from_loader(name, loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(path)
    exec(compile(text, str(path), "exec"), mod.__dict__)
    return mod


class Strategy:
    """A strategy folder: strategy.json (settings, risk, words) + strategy.py (the buy/sell logic)."""

    def __init__(self, data, folder=None, script=None):
        self.data, self.folder = data, Path(folder) if folder else None
        self.path = self.folder / SETTINGS_FILE if self.folder else None
        for key in ("id", "name", "version", "universe", "execution"):
            if key not in data:
                raise StrategyError(f"missing '{key}' in {SETTINGS_FILE}")
        self.id, self.name, self.version = data["id"], data["name"], data["version"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", self.id):
            raise StrategyError("id must be lowercase letters, digits and dashes, e.g. 'my-pullback'")
        self.params = {k: v for k, v in (data.get("params") or {}).items()}
        for k, v in self.params.items():
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise StrategyError(f"parameter '{k}' must be a number")
        u = data["universe"]
        if u.get("index") not in UNIVERSES:
            raise StrategyError(f"universe.index must be one of {sorted(UNIVERSES)}")
        self.universe = u["index"]
        self.min_price, self.min_turnover_cr = u.get("min_price"), u.get("min_turnover_cr")
        self.max_rank = _num_or_param(data["max_rank"], self.params, "max_rank") if data.get("max_rank") is not None else None
        self.history = int(data.get("history_sessions") or 260)
        self.max_hold_days = data.get("max_hold_days") or None
        self.stop = self._check_stop(data.get("stop"))
        self.target = self._check_target(data.get("target"))
        self.trail = self._check_trail(data.get("trail"))
        ex = data["execution"]
        for k in ("limit_buffer_pct", "max_gap_pct"):
            if not isinstance(ex.get(k), (int, float)):
                raise StrategyError(f"execution.{k} is required (a number)")
        self.limit_buffer_pct, self.max_gap_pct = float(ex["limit_buffer_pct"]), float(ex["max_gap_pct"])
        self.tunable = data.get("tunable") or {}
        for k, rng in self.tunable.items():
            if k not in self.params or not (isinstance(rng, list) and len(rng) == 2):
                raise StrategyError(f"tunable '{k}' must be a parameter with [low, high]")
        # The bot trades only a risk block the user confirmed on the review screen. Unconfirmed strategies
        # (drafts, ready-made files) are backtested with the suggestions plus any answers given so far.
        raw = data.get("risk") or {}
        self.risk_confirmed = bool(raw.get("confirmed"))
        if self.risk_confirmed:
            r, self.risk_source = raw, "user"
        else:
            answered = {k: raw[k] for k in RISK_FIELDS if k in raw}
            r = dict(data.get("risk_suggested") or {}, **answered)
            if all(k in r for k in RISK_FIELDS):
                self.risk_source = "draft answers + suggestions" if answered else "suggested"
            elif "sizing" in r and "max_positions" in r:
                # enough for a first backtest: the limits the user hasn't answered yet are simply not applied
                r = dict({"max_position_pct": None, "weekly_loss_limit_pct": None, "halt_drawdown_pct": None,
                          "regime": None, "broker_stop_required": self.stop is not None, "stops_only_up": True}, **r)
                self.risk_source = "sizing answers only (limits not applied yet)"
            else:
                r, self.risk_source = None, None
        self.risk = self._check_risk(r)
        self.regime = None
        if self.risk and self.risk.get("regime"):
            try:
                self.regime = parse(self.risk["regime"])
                check(self.regime, {}, index_only=True, text=self.risk["regime"])
            except RuleError as e:
                raise StrategyError(f"risk.regime: {e}")
        # the logic script
        if script is None:
            if self.folder is None or not (self.folder / SCRIPT_FILE).exists():
                raise StrategyError(f"no {SCRIPT_FILE} next to {SETTINGS_FILE}")
            script = (self.folder / SCRIPT_FILE).read_text()
        self.script = script
        problems = check_script(script, data.get("allowed_imports") or ())
        if problems:
            raise StrategyError("strategy script: " + "; ".join(problems))
        try:
            mod = _load_module(self.folder / SCRIPT_FILE if self.folder else self.id, script)
        except Exception as e:  # noqa: BLE001 - report any error in the user's script plainly
            raise StrategyError(f"strategy script failed to load: {type(e).__name__}: {e}")
        if not callable(getattr(mod, "prepare", None)):
            raise StrategyError("the script must define prepare(stock, market, p)")
        self._prepare = mod.prepare
        self._allow = getattr(mod, "allow_entry", None)   # optional: portfolio-level entry rules
        self._manage = getattr(mod, "manage", None)       # optional: rules for an open position
        self.has_hooks = bool(self._allow or self._manage)
        if self.stop and self.stop["type"] == "level" or self.trail and self.trail["type"] == "level":
            pass  # checked when the script runs (stop_level / trail_level must be returned)

    # -------- settings checks

    def _check_stop(self, st):
        if st is None:
            return None
        t = st.get("type")
        out = {"type": t, "min_pct": st.get("min_pct"), "max_pct": st.get("max_pct"),
               "buffer_pct": float(st.get("buffer_pct", 0) or 0)}
        if t == "pct":
            out["value"] = _num_or_param(st.get("value"), self.params, "stop.value")
        elif t == "atr":
            out["mult"] = _num_or_param(st.get("mult"), self.params, "stop.mult")
            out["period"] = int(_num_or_param(st.get("period", 14), self.params, "stop.period"))
        elif t != "level":
            raise StrategyError("stop.type must be pct, atr or level (level = the script's stop_level), or stop: null")
        return out

    def _check_target(self, tg):
        if tg is None:
            return None
        t = tg.get("type")
        if t not in ("r", "pct"):
            raise StrategyError("target.type must be r or pct (or target: null)")
        if t == "r" and self.stop is None:
            raise StrategyError("a 1:R target is measured from the stop-loss, so it needs a stop; use a % target or add a stop")
        return {"type": t, "value": _num_or_param(tg.get("value"), self.params, "target.value")}

    def _check_trail(self, tr):
        if tr is None:
            return None
        t = tr.get("type")
        out = {"type": t, "buffer_pct": float(tr.get("buffer_pct", 0) or 0)}
        if t == "atr":
            out["mult"] = _num_or_param(tr.get("mult"), self.params, "trail.mult")
            out["period"] = int(_num_or_param(tr.get("period", 14), self.params, "trail.period"))
        elif t == "pct":
            out["value"] = _num_or_param(tr.get("value"), self.params, "trail.value")
        elif t != "level":
            raise StrategyError("trail.type must be atr, pct or level (level = the script's trail_level), or trail: null")
        return out

    def _check_risk(self, r):
        if r is None:
            return None
        missing = [k for k in RISK_FIELDS if k not in r]
        if missing:
            raise StrategyError(f"risk is missing {missing}. Every risk field must be answered (null = none)")
        sz = r["sizing"] or {}
        if sz.get("type") not in ("risk", "fixed_pct") or not isinstance(sz.get("pct"), (int, float)) or sz["pct"] <= 0:
            raise StrategyError("risk.sizing must be {'type': 'risk' | 'fixed_pct', 'pct': number > 0}")
        if sz["type"] == "risk" and self.stop is None:
            raise StrategyError("risk.sizing 'risk' needs a stop; with no stop use 'fixed_pct' sizing")
        if r["broker_stop_required"] and self.stop is None:
            raise StrategyError("risk.broker_stop_required is true but the strategy has no stop")
        for k in ("max_positions", "max_position_pct", "weekly_loss_limit_pct", "halt_drawdown_pct"):
            v = r[k]
            if v is not None and (not isinstance(v, (int, float)) or v <= 0):
                raise StrategyError(f"risk.{k} must be a positive number or null")
        for k in ("broker_stop_required", "stops_only_up"):
            if not isinstance(r[k], bool):
                raise StrategyError(f"risk.{k} must be true or false")
        return r

    # -------- running the script

    def run(self, bars, market):
        """Run the script on one stock's history -> Result (raises StrategyError with a plain message)."""
        try:
            out = self._prepare(bars, market, dict(self.params))
        except Exception as e:  # noqa: BLE001
            raise StrategyError(f"the strategy script failed on {bars.symbol}: {type(e).__name__}: {e}")
        return Result(None, out, len(bars), self.id)

    def allow(self, candidate, book):
        """None if the script's allow_entry() lets this candidate be bought, else the reason it blocks it."""
        if not self._allow:
            return None
        try:
            r = self._allow(dict(candidate), book, dict(self.params))
        except Exception as e:  # noqa: BLE001
            raise StrategyError(f"the strategy's allow_entry() failed on {candidate.get('symbol')}: {type(e).__name__}: {e}")
        if r is True or r is None:
            return None
        return r if isinstance(r, str) and r else "blocked_by_strategy_rule"

    def manage_position(self, position, bars):
        """The script's manage() for one open position on its last day: {} or {"sell": reason} and/or {"stop": price}."""
        if not self._manage:
            return {}
        try:
            r = self._manage(dict(position), bars, dict(self.params))
        except Exception as e:  # noqa: BLE001
            raise StrategyError(f"the strategy's manage() failed on {position.get('symbol')}: {type(e).__name__}: {e}")
        if not r:
            return {}
        if not isinstance(r, dict):
            raise StrategyError("manage() must return None or a dict like {'sell': 'reason'} or {'stop': price}")
        out = {}
        if r.get("sell"):
            out["sell"] = r["sell"] if isinstance(r["sell"], str) else "position_rule"
        if r.get("stop") is not None:
            if not isinstance(r["stop"], (int, float)) or r["stop"] != r["stop"] or r["stop"] <= 0:
                raise StrategyError(f"manage() returned an invalid stop {r['stop']!r}")
            out["stop"] = float(r["stop"])
        return out

    def run_series(self, s, panel, index):
        bars = Bars.from_series(s, panel.dates)
        res = self.run(bars, Market(panel, index, bars))
        res.s = s
        if self.stop and self.stop["type"] == "level" and res.stop_level is None:
            raise StrategyError("stop type is 'level' but the script returns no stop_level")
        if self.trail and self.trail["type"] == "level" and res.trail_level is None:
            raise StrategyError("trail type is 'level' but the script returns no trail_level")
        return res

    def plan_stop(self, res, i, limit_px):
        """(absolute stop level or None, stop distance or None, skip reason or None) for a signal on row i.
        pct / atr stops are a distance measured from the fill; level stops are a fixed price."""
        st = self.stop
        if st is None:
            return None, None, None
        close = res.s.c[i]
        level = None
        if st["type"] == "pct":
            dist = close * st["value"] / 100
        elif st["type"] == "atr":
            a = res.atr(st["period"])[i]
            if not ok(a):
                return None, None, "no_history"
            dist = st["mult"] * a
        else:
            v = res.stop_level[i]
            if not isinstance(v, (int, float)) or not ok(v):
                return None, None, "no_stop_level"
            level = v * (1 - st["buffer_pct"] / 100)
            dist = limit_px - level
        pct = dist / limit_px * 100
        if st.get("max_pct") is not None and pct > st["max_pct"]:
            return level, dist, "stop_too_wide"
        if st.get("min_pct") is not None and pct < st["min_pct"]:
            dist = limit_px * st["min_pct"] / 100
            level = limit_px - dist if level is not None else None
        if dist <= 0:
            return level, dist, "stop_above_entry"
        return level, dist, None

    def target_price(self, fill, stop_px):
        t = self.target
        if t is None:
            return None
        if t["type"] == "r":
            return fill + t["value"] * (fill - stop_px) if stop_px is not None else None
        return fill * (1 + t["value"] / 100)

    def trail_level(self, res, i, highest_close):
        tr = self.trail
        if tr is None:
            return None
        if tr["type"] == "atr":
            a = res.atr(tr["period"])[i]
            return highest_close - tr["mult"] * a if ok(a) else None
        if tr["type"] == "pct":
            return highest_close * (1 - tr["value"] / 100)
        v = res.trail_level[i]
        return v * (1 - tr["buffer_pct"] / 100) if isinstance(v, (int, float)) and ok(v) else None

    def size(self, equity, limit_px, stop_dist):
        """Shares for a new position, from the risk block (confirmed or suggested)."""
        r = self.risk
        sz = r["sizing"]
        if sz["type"] == "risk":
            qty = int(equity * sz["pct"] / 100 // stop_dist) if stop_dist and stop_dist > 0 else 0
        else:
            qty = int(equity * sz["pct"] / 100 // limit_px)
        if r.get("max_position_pct"):
            qty = min(qty, int(equity * r["max_position_pct"] / 100 // limit_px))
        return max(qty, 0)

    def universe_checks(self, s, i):
        """Failed universe filters on row i (price, liquidity)."""
        fail = []
        if self.min_price is not None and s.c[i] < self.min_price:
            fail.append("price")
        if self.min_turnover_cr is not None:
            n = 20
            if i + 1 < n or sum(s.t[i - n + 1:i + 1]) / n / 1e7 < self.min_turnover_cr:
                fail.append("liquidity")
        return fail


def lookahead_check(st, panel, index, keys, max_stocks=40, cuts_per_stock=8):
    """Re-run the script with history cut off at earlier days; every answer up to the cut must be identical.
    A difference means the script used data from after the day it was deciding (look-ahead).
    Cut-offs are chosen where look-ahead would show: days where a rule, buy or sell fires, plus random days."""
    import random
    rng = random.Random(7)
    pool = [k for k in keys if k in panel.series and len(panel.series[k].c) > st.history + 40]
    sample = rng.sample(pool, min(max_stocks, len(pool)))
    tested = 0

    def same(x, y):
        if x == y:
            return True
        return isinstance(x, float) and isinstance(y, float) and \
            ((x != x and y != y) or abs(x - y) <= 1e-9 * max(1.0, abs(x)))
    for key in sample:
        s = panel.series[key]
        full_bars = Bars.from_series(s, panel.dates)
        full = st.run(full_bars, Market(panel, index, full_bars))
        n = len(s.c)
        lo = min(st.history, n - 2)
        fired = [i for i in range(lo, n - 1)
                 if full.buy[i] or (full.sell and full.sell[i]) or any(col[i] for col in full.checks.values())]
        cut_days = set(rng.sample(fired, min(cuts_per_stock - 2, len(fired))))
        cut_days |= {rng.randrange(lo, n - 1) for _ in range(2)}
        for c in sorted(cut_days):
            bars = Bars.from_series(s, panel.dates, c)
            part = st.run(bars, Market(panel, index, bars))
            pairs = [("buy", full.buy, part.buy), ("score", full.score, part.score), ("sell", full.sell, part.sell),
                     ("stop_level", full.stop_level, part.stop_level), ("trail_level", full.trail_level, part.trail_level)]
            pairs += [(f"check '{k}'", full.checks[k], part.checks.get(k)) for k in full.checks]
            for name, x_all, y_all in pairs:
                if x_all is None:
                    continue
                for i in range(c - 5 if c >= 5 else 0, c + 1):  # the last days before the cut are where it shows
                    if y_all is None or not same(x_all[i], y_all[i]):
                        return {"ok": False, "stock": s.sym, "output": name, "day": full_bars.date[i].isoformat(),
                                "problem": (f"{name} for {s.sym} on {full_bars.date[i]} changes when later days are added "
                                            f"({x_all[i]} vs {y_all[i] if y_all is not None else 'missing'}): the script "
                                            "looks at data from after the day it decides. Fix it to use only that day and earlier.")}
            tested += 1
    return {"ok": True, "stocks_tested": len(sample), "cut_offs_tested": tested}

def fingerprint(data, script, rules_only=False):
    """Short hash of a strategy (settings + script). rules_only: everything except the risk answers."""
    import hashlib
    body = {k: v for k, v in data.items() if not k.startswith("_") and not (rules_only and k == "risk")}
    return hashlib.sha256((json.dumps(body, sort_keys=True, default=str) + "\n" + script).encode()).hexdigest()[:16]


def sector_map():
    """symbol -> industry, from every cached NSE index list."""
    import csv
    import io
    out = {}
    for p in (HOME / "data").glob("*_list.csv"):
        for r in csv.DictReader(io.StringIO(p.read_text(encoding="utf-8", errors="replace"))):
            sym = (r.get("Symbol") or "").strip()
            if sym and sym not in out:
                out[sym] = (r.get("Industry") or "").strip()
    return out

# ---------------------------------------------------------------- the user's settings


def read_json(p, default=None):
    return json.loads(Path(p).read_text()) if Path(p).exists() else default


def write_json(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str, ensure_ascii=False))
    tmp.replace(p)


def _ids(root):
    return sorted(p.parent.name for p in root.glob(f"*/{SETTINGS_FILE}")) if root.exists() else []


def library_ids():
    return _ids(LIBRARY)


def user_ids():
    return _ids(USER_STRATEGIES)


# v1.0 kept the momentum settings as flat keys in config.json -> "strategy"
_LEGACY_PARAMS = {"rank_entry", "vol_mult", "rsi_min", "rsi_max", "max_ext_pct", "stop_pct", "target_r"}


def config():
    """config.json, moved to the multi-strategy layout on first use (backup kept as config.v1.backup.json)."""
    c = read_json(CONFIG)
    if c is None or "active_strategy" in c:
        return c
    shutil.copy(CONFIG, HOME / "config.v1.backup.json")
    lib = LIBRARY / LEGACY_ID
    st = read_json(lib / SETTINGS_FILE)
    st["version"] = c.get("version", st["version"])
    legacy = c.get("strategy") or {}
    for k, v in legacy.items():
        if k in _LEGACY_PARAMS:
            st["params"][k] = v
        elif k in ("limit_buffer_pct", "max_gap_pct"):
            st["execution"][k] = v
    sug = st["risk_suggested"]
    lim = c.get("limits") or {}
    st["risk"] = {
        "sizing": {"type": "risk", "pct": legacy.get("risk_pct", sug["sizing"]["pct"])},
        "max_positions": legacy.get("max_positions", sug["max_positions"]),
        "max_position_pct": legacy.get("max_position_pct", sug["max_position_pct"]),
        "weekly_loss_limit_pct": lim.get("weekly_loss_pct", sug["weekly_loss_limit_pct"]),
        "halt_drawdown_pct": lim.get("halt_drawdown_pct", sug["halt_drawdown_pct"]),
        "regime": sug["regime"] if legacy.get("use_regime", True) else None,
        "broker_stop_required": True, "stops_only_up": True,
        # pre-filled from v1.0 but NOT confirmed: the user reviews and confirms them on the review screen
        # (the first trading-day after the update does this) before any new entry
    }
    dest = USER_STRATEGIES / LEGACY_ID
    script = (lib / SCRIPT_FILE).read_text()
    write_json(dest / SETTINGS_FILE, st)
    (dest / SCRIPT_FILE).write_text(script)
    for k in ("version", "limits", "strategy"):
        c.pop(k, None)
    c["active_strategy"] = LEGACY_ID
    c["migrated_to_multi_strategy"] = _today()
    write_json(CONFIG, c)
    return c


def active_id():
    c = config()
    return (c or {}).get("active_strategy")


def strategy_folder(ref):
    """Folder for a strategy id (the user's copy first, then the library) or a folder / strategy.json path."""
    if ref and (os.sep in ref or ref.endswith(".json")):
        p = Path(ref).expanduser()
        return p.parent if p.name == SETTINGS_FILE or p.suffix == ".json" else p
    for root in (USER_STRATEGIES, LIBRARY):
        if (root / ref / SETTINGS_FILE).exists():
            return root / ref
    raise StrategyError(f"no strategy '{ref}'. Ready-made: {library_ids()}; yours: {user_ids()}")


def strategy_path(ref):
    return strategy_folder(ref) / SETTINGS_FILE


def load(ref=None):
    """Load a strategy by id or folder; default = the active strategy."""
    if ref is None:
        ref = active_id()
        if not ref:
            raise StrategyError("no active strategy yet - choose one with the strategies skill")
    folder = strategy_folder(ref)
    data = read_json(folder / SETTINGS_FILE)
    if data is None:
        raise StrategyError(f"no {SETTINGS_FILE} in {folder}")
    return Strategy(data, folder)


def save_user_strategy(data, script):
    """Validate and save a strategy (settings + script) into ~/ai-trader/strategies/<id>/."""
    Strategy(data, None, script)
    dest = USER_STRATEGIES / data["id"]
    dest.mkdir(parents=True, exist_ok=True)
    write_json(dest / SETTINGS_FILE, data)
    (dest / SCRIPT_FILE).write_text(script)
    return dest
