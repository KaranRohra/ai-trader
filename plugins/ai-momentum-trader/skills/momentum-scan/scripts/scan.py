#!/usr/bin/env python3
"""Momentum Breakout scanner for the NIFTY 500 (Python standard library only).

Data: free, official NSE archive files (daily bhavcopy + index closes + NIFTY 500 list).
No broker login, no API key. Everything is cached in ~/ai-momentum-trader/data.

Commands
  update                 download any missing days (first run: ~15 months, then 1 file/day)
  scan                   today's regime, entry signals, and exit/trail updates for open positions (JSON)
  outcomes               fill forward returns (+5/+10/+20/+60 sessions) into the shadow book
  backtest               replay the exact same rules over history (see --help)

The rules live in DEFAULTS below and can be overridden by config.json -> "strategy".
"""
import argparse
import csv
import datetime as dt
import gzip
import io
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from array import array
from bisect import bisect_left
from collections import deque
from pathlib import Path

HOME = Path(os.environ.get("CMT_HOME", Path.home() / "ai-momentum-trader"))
DATA = HOME / "data"
EQ_DIR = DATA / "eq"
INDEX_FILE = DATA / "index_nifty500.json"
HOLIDAYS_FILE = DATA / "no_data_days.json"
N500_FILE = DATA / "nifty500_list.csv"
SHADOW = HOME / "learning" / "shadow_book.csv"

UA = "Mozilla/5.0 (compatible; ai-momentum-trader/1.0; daily NSE archive download)"
UDIFF_START = dt.date(2024, 7, 8)  # NSE switched the bhavcopy format on this date
KEEP_SERIES = {"EQ", "BE", "BZ"}
INDEX_NAMES = {"nifty 500", "cnx 500"}

DEFAULTS = {
    "version": "v1.0",
    "min_price": 50.0,              # ₹
    "min_turnover_cr": 10.0,        # 20-day average traded value, ₹ crore
    "regime_sma": 200,              # NIFTY 500 below its 200-DMA -> no new entries
    "trend_fast": 50,
    "trend_slow": 200,
    "mom_short": 126,               # ~6 months of sessions
    "mom_long": 252,                # ~12 months
    "mom_w_short": 0.5,
    "mom_w_long": 0.5,
    "rank_entry": 50,               # must rank in the top 50 by momentum score
    "rank_hold": 100,               # exit if rank falls below 100
    "breakout_lookback": 252,       # 52-week high (previous 252 sessions)
    "vol_avg": 50,
    "vol_mult": 1.5,
    "rsi_period": 14,
    "rsi_min": 60.0,
    "rsi_max": 80.0,
    "ema_ext": 20,
    "max_ext_pct": 10.0,            # close at most 10% above the 20-EMA
    "atr_period": 14,
    # exits (picked by the user in the Strategy Lab portal, 2026-11-03): fixed 14% stop-loss and a fixed
    # 1:2 target (= +28%), both held at Zerodha as one two-leg (OCO) GTT. No trailing, no rule exits.
    # Chosen from the middle of a stable area (13-16% stop with 1:2 all gave 16.6-20.1% CAGR).
    "stop_mode": "pct",             # pct | low<N> | prev_week_low | breakout_level | atr
    "stop_buffer_pct": 0.5,         # stop sits 0.5% below the chart level
    "min_stop_pct": 3.0,            # tighter stops are widened to 3%
    "max_stop_pct": 15.0,           # wider than 15% -> skip the trade
    "target_r": 2.0,                # target = entry + 2 x (entry - stop)
    "stop_atr": 2.0,                # only used when stop_mode = "atr"
    "trail_atr": 0.0,               # 0 = no trailing stop
    "exit_on_50dma": False,
    "exit_on_rank": False,
    "max_hold_days": 0,
    "stop_pct": 14.0,               # stop = entry - 14% of the signal close
    "target_pct": 0.0,              # alternative to target_r: fixed % above entry
    # backtest/portal research switches (the live bot uses risk-based sizing and the regime filter)
    "use_regime": True,
    "position_pct": 0.0,            # >0: fixed % of equity per position instead of risk-based sizing
    "idle_in_index": False,         # backtest only: uninvested cash earns the NIFTY 500 index return
    "idle_only_regime_on": False,   # ...but move it out of the ETF while the regime filter is off
    "cash_yield_pct": 0.0,          # yield on cash not in the ETF (e.g. 6 = liquid fund)
    "risk_pct": 2.0,                # 2% risk / 14% stop -> each position ~14.3% of equity
    "max_positions": 10,            # in practice cash limits it to ~7 open positions
    "max_position_pct": 100.0,
    "limit_buffer_pct": 1.0,        # LIMIT buy at signal close + 1%
    "max_gap_pct": 3.0,             # skip if it opens > 3% above the signal close
}

# ---------------------------------------------------------------- utilities

def log(*a):
    print(*a, file=sys.stderr, flush=True)


def load_config(path=None):
    cfg = dict(DEFAULTS)
    p = Path(path) if path else HOME / "config.json"
    if p.exists():
        raw = json.loads(p.read_text())
        cfg.update(raw.get("strategy", raw if path else {}))
        if "version" in raw:
            cfg["version"] = raw["version"]
    return cfg


_USE_CURL = False


def _fetch_once(url, timeout):
    global _USE_CURL
    if not _USE_CURL:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return None
            raise
        except urllib.error.URLError as e:
            if "CERTIFICATE_VERIFY_FAILED" not in str(e) or not shutil.which("curl"):
                raise
            _USE_CURL = True  # this Python has no CA bundle; the system curl has one
    out = subprocess.run(["curl", "-s", "-f", "-L", "--max-time", str(timeout), "-A", UA, url],
                         capture_output=True)
    if out.returncode == 22:  # HTTP error (404 on holidays)
        return None
    if out.returncode == 56:  # NSE resets the connection for files that don't exist (holidays)
        raise ConnectionResetError(url)
    if out.returncode != 0:
        raise RuntimeError(f"curl failed ({out.returncode}) for {url}")
    return out.stdout


def fetch(url, timeout=30, tries=3):
    """Return bytes, or None if the file doesn't exist (404). Retries network errors."""
    for n in range(tries):
        try:
            return _fetch_once(url, timeout)
        except Exception:
            if n == tries - 1:
                raise
            time.sleep(1.5 * (n + 1))


def tick_round(price, up=False):
    """Round to a price grid that is valid on NSE for that price band (coarse = always valid)."""
    t = 0.05 if price < 1000 else 0.10 if price < 5000 else 0.50 if price < 10000 else 1.0 if price < 20000 else 5.0
    n = price / t
    n = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    return round(n * t, 2)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0

# ---------------------------------------------------------------- download + cache

def _bhav_urls(d):
    if d >= UDIFF_START:
        return [f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"]
    mon = d.strftime("%b").upper()
    return [f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{d.year}/{mon}/cm{d:%d}{mon}{d.year}bhav.csv.zip"]


def _parse_bhav(raw):
    z = zipfile.ZipFile(io.BytesIO(raw))
    text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    rows = []
    rd = csv.DictReader(io.StringIO(text))
    for r in rd:
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        if "TckrSymb" in r:
            ser = r.get("SctySrs", "")
            if ser not in KEEP_SERIES:
                continue
            rows.append((r["TckrSymb"], r.get("ISIN", ""), ser, r["OpnPric"], r["HghPric"], r["LwPric"],
                         r["ClsPric"], r["TtlTradgVol"], r["TtlTrfVal"]))
        else:
            ser = r.get("SERIES", "")
            if ser not in KEEP_SERIES:
                continue
            rows.append((r["SYMBOL"], r.get("ISIN", ""), ser, r["OPEN"], r["HIGH"], r["LOW"],
                         r["CLOSE"], r["TOTTRDQTY"], r["TOTTRDVAL"]))
    return rows


def _parse_index(raw):
    for r in csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))):
        name = (r.get("Index Name") or "").strip().lower()
        if name in INDEX_NAMES:
            return _f(r.get("Closing Index Value"))
    return None


def _jload(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def update(start=None, end=None, quiet=False):
    """Download every missing weekday between start and end (inclusive)."""
    EQ_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()
    end = end or today
    start = start or (end - dt.timedelta(days=460))  # ~15 months -> >300 sessions
    index = _jload(INDEX_FILE, {})
    nodata = set(_jload(HOLIDAYS_FILE, []))
    todo, d = [], start
    while d <= end:
        key = d.strftime("%Y%m%d")
        if d.weekday() < 5 and key not in nodata and not ((EQ_DIR / f"{key}.csv.gz").exists() and key in index):
            todo.append(d)
        d += dt.timedelta(days=1)
    if todo and not quiet:
        log(f"update: fetching {len(todo)} day(s) from NSE archives ({todo[0]} .. {todo[-1]})")

    def get(url):
        try:
            return fetch(url), None
        except Exception as e:
            return None, e

    def one(day):
        bhav, e1 = get(_bhav_urls(day)[0])
        idx, e2 = get(f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{day:%d%m%Y}.csv")
        time.sleep(0.2)  # be polite to NSE's servers
        return day, bhav, idx, e1 or e2

    from concurrent.futures import ThreadPoolExecutor
    n_new = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for day, bhav, idx, err in pool.map(one, todo):
            key = day.strftime("%Y%m%d")
            if bhav is None:
                # both files missing (404 or connection reset) for a past date -> market holiday
                if idx is None and (today - day).days > 3:
                    nodata.add(key)
                elif (today - day).days <= 3:
                    pass  # today's file is published after ~6 PM; try again next run
                else:
                    log(f"  could not download {day} ({err}); will retry next run")
                continue
            with gzip.open(EQ_DIR / f"{key}.csv.gz", "wt", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["sym", "isin", "ser", "o", "h", "l", "c", "v", "t"])
                w.writerows(_parse_bhav(bhav))
            close = _parse_index(idx) if idx else None
            if close:
                index[key] = close
            n_new += 1
            if not quiet and n_new % 100 == 0:
                log(f"  {n_new} days done (at {day})")
    fetched = len(todo)
    INDEX_FILE.write_text(json.dumps(dict(sorted(index.items()))))
    HOLIDAYS_FILE.write_text(json.dumps(sorted(nodata)))
    # NIFTY 500 constituents: refresh weekly
    if not N500_FILE.exists() or time.time() - N500_FILE.stat().st_mtime > 7 * 86400:
        raw = fetch("https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv")
        if raw:
            N500_FILE.write_bytes(raw)
    if not quiet:
        log(f"update: {n_new} new trading days cached (checked {fetched} weekdays)")
    return n_new

# ---------------------------------------------------------------- panel (adjusted series)

def _ca_factors():
    fs = {0.1, 0.2, 0.25, 0.4, 0.5}  # face-value splits 10->1, 5->1, 4->1, 5->2, 2->1
    for a in range(1, 6):  # bonus a:b  -> price factor b/(a+b)
        for b in range(1, 6):
            fs.add(b / (a + b))
    fs = {round(f, 4) for f in fs if f < 0.8}
    return sorted(fs) + sorted({round(1 / f, 4) for f in (0.1, 0.2, 0.5)})  # reverse splits


CA_FACTORS = _ca_factors()


def _detect_ca(prev_close, o, c):
    """Return a split/bonus price factor if the overnight jump matches one, else None.
    NSE stocks have <=20% daily bands, so a >20% overnight jump that lands on a standard
    split/bonus ratio (and a normal day after adjusting) is a corporate action."""
    if prev_close <= 0 or o <= 0 or c <= 0:
        return None
    r = o / prev_close
    if 0.8 <= r <= 1.25:
        return None
    for f in CA_FACTORS:
        if abs(r / f - 1) < 0.025 and 0.8 <= (c / prev_close) / f <= 1.2:
            return f
    # Any other overnight gap beyond -35% / +60% can't come from trading inside NSE's price bands:
    # it's a special dividend / demerger / capital reduction. Adjust by the actual ratio.
    if r < 0.65 or r > 1.6:
        return round(r, 4)
    return None


NAN = float("nan")


def ok(x):
    return x == x  # False for NaN


def _r(x, n):
    return round(x, n) if ok(x) else None


class Series:
    __slots__ = ("sym", "isin", "di", "o", "h", "l", "c", "v", "t", "ca")

    def __init__(self, sym, isin):
        self.sym, self.isin = sym, isin
        self.di = array("i")
        self.o, self.h, self.l, self.c, self.v, self.t = (array("d") for _ in range(6))
        self.ca = []  # (row position, factor, date index)

    def row(self, k):
        """Row number for date index k, or None if the stock didn't trade that day."""
        i = bisect_left(self.di, k)
        return i if i < len(self.di) and self.di[i] == k else None


class Panel:
    """All cached days loaded as split/bonus-adjusted per-stock series, keyed by ISIN."""

    def __init__(self, start=None, end=None, symbols=None, quiet=True):
        files = sorted(EQ_DIR.glob("*.csv.gz"))
        index = _jload(INDEX_FILE, {})
        days = [f.name[:8] for f in files]
        if start:
            days = [d for d in days if d >= start.strftime("%Y%m%d")]
        if end:
            days = [d for d in days if d <= end.strftime("%Y%m%d")]
        self.dates = [dt.datetime.strptime(d, "%Y%m%d").date() for d in days]
        self.index = [index.get(d) for d in days]
        self.series = {}
        sym_key = {}  # symbol -> current key (handles ISIN change on the same symbol)
        for k, d in enumerate(days):
            if not quiet and k % 250 == 0:
                log(f"  loading {d} ({k}/{len(days)})")
            with gzip.open(EQ_DIR / f"{d}.csv.gz", "rt") as fh:
                rd = csv.reader(fh)
                next(rd)
                for sym, isin, ser, o, h, l, c, v, t in rd:
                    if symbols is not None and sym not in symbols and isin not in symbols:
                        continue
                    if "-RE" in sym:  # rights entitlements
                        continue
                    key = isin or sym
                    old = sym_key.get(sym)
                    if old and old != key and key not in self.series and old in self.series:
                        self.series[key] = self.series.pop(old)  # new ISIN, same company
                    s = self.series.get(key)
                    if s is None:
                        s = self.series[key] = Series(sym, isin)
                    elif len(s.di) and s.di[-1] == k:
                        continue  # duplicate row (e.g. EQ + BE same day)
                    s.sym = sym
                    sym_key[sym] = key
                    of, cf = _f(o), _f(c)
                    if len(s.c):
                        f = _detect_ca(s.c[-1], of, cf)
                        if f:
                            s.ca.append((len(s.c), f, k))
                    s.di.append(k)
                    s.o.append(of)
                    s.h.append(_f(h))
                    s.l.append(_f(l))
                    s.c.append(cf)
                    s.v.append(_f(v))
                    s.t.append(_f(t))
        for s in self.series.values():
            if s.ca:
                self._adjust(s)
        self.by_sym = {s.sym: s for s in self.series.values()}

    @staticmethod
    def _adjust(s):
        events = {pos: f for pos, f, _ in s.ca}
        cum = 1.0
        for i in range(len(s.c) - 1, -1, -1):
            if i + 1 in events:
                cum *= events[i + 1]
            if cum != 1.0:
                s.o[i] *= cum
                s.h[i] *= cum
                s.l[i] *= cum
                s.c[i] *= cum
                s.v[i] /= cum

# ---------------------------------------------------------------- indicators (full arrays, NaN = not enough history)

def _nan(n):
    return array("d", [NAN]) * n


def sma(x, n):
    out, s = _nan(len(x)), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(x, n):
    out, a, e = _nan(len(x)), 2.0 / (n + 1), 0.0
    for i, v in enumerate(x):
        if i == n - 1:
            e = sum(x[:n]) / n
            out[i] = e
        elif i >= n:
            e = e + a * (v - e)
            out[i] = e
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


def prior_max(x, n):
    """max of the previous n values (excluding today)."""
    out, q = _nan(len(x)), deque()
    for i in range(len(x)):
        while q and q[0] < i - n:
            q.popleft()
        if i >= n:
            out[i] = x[q[0]]
        while q and x[q[-1]] <= x[i]:
            q.pop()
        q.append(i)
    return out


def prior_mean(x, n):
    out, s = _nan(len(x)), 0.0
    for i in range(len(x)):
        if i >= n:
            out[i] = s / n
            s -= x[i - n]
        s += x[i]
    return out


def momentum(c, cfg):
    """(w_s * 6M return + w_l * 12M return) / annualized volatility of daily log returns (12M)."""
    ns, nl = cfg["mom_short"], cfg["mom_long"]
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
            r6 = c[i] / c[i - ns] - 1
            r12 = c[i] / c[i - nl] - 1
            out[i] = (cfg["mom_w_short"] * r6 + cfg["mom_w_long"] * r12) / vol
    return out


class Ind:
    """All indicators for one series, aligned to its rows."""
    __slots__ = ("s", "sma_f", "sma_s", "ema", "rsi", "atr", "hi", "vavg", "tavg", "mom")

    def __init__(self, s, cfg):
        self.s = s
        self.sma_f = sma(s.c, cfg["trend_fast"])
        self.sma_s = sma(s.c, cfg["trend_slow"])
        self.ema = ema(s.c, cfg["ema_ext"])
        self.rsi = rsi(s.c, cfg["rsi_period"])
        self.atr = atr(s.h, s.l, s.c, cfg["atr_period"])
        self.hi = prior_max(s.h, cfg["breakout_lookback"])
        self.vavg = prior_mean(s.v, cfg["vol_avg"])
        self.tavg = sma(s.t, 20)
        self.mom = momentum(s.c, cfg)

    def row(self, k):
        return self.s.row(k)


def evaluate(ind, i, rank, cfg):
    """Check every entry rule on row i. Returns (features, failed_rules)."""
    s = ind.s
    c = s.c[i]
    f = {
        "symbol": s.sym, "close": round(c, 2), "rank": rank,
        "score": _r(ind.mom[i], 4), "rsi": _r(ind.rsi[i], 1), "atr": _r(ind.atr[i], 2),
        "sma50": _r(ind.sma_f[i], 2), "sma200": _r(ind.sma_s[i], 2), "high52_prior": _r(ind.hi[i], 2),
        "vol_ratio": _r(s.v[i] / ind.vavg[i], 2) if ok(ind.vavg[i]) and ind.vavg[i] > 0 else None,
        "turnover_cr": _r(ind.tavg[i] / 1e7, 1),
        "ext_pct": _r((c / ind.ema[i] - 1) * 100, 1),
        "breakout_pct": _r((c / ind.hi[i] - 1) * 100, 2),
    }
    if not all(ok(x) for x in (ind.mom[i], ind.rsi[i], ind.atr[i], ind.sma_s[i], ind.hi[i], ind.vavg[i], ind.ema[i])):
        return f, ["history<1y"]
    return f, failed_rules(ind, i, rank, cfg)


def failed_rules(ind, i, rank, cfg):
    s, c, fail = ind.s, ind.s.c[i], []
    if c < cfg["min_price"]:
        fail.append("price")
    if ind.tavg[i] / 1e7 < cfg["min_turnover_cr"]:
        fail.append("liquidity")
    if not (c > ind.sma_f[i] > ind.sma_s[i]):
        fail.append("trend")
    if rank is None or rank > cfg["rank_entry"]:
        fail.append("rank")
    if not c > ind.hi[i]:
        fail.append("no_52w_breakout")
    if not s.v[i] >= cfg["vol_mult"] * ind.vavg[i]:
        fail.append("volume")
    if not (cfg["rsi_min"] <= ind.rsi[i] <= cfg["rsi_max"]):
        fail.append("rsi")
    if (c / ind.ema[i] - 1) * 100 > cfg["max_ext_pct"]:
        fail.append("extended")
    return fail


def regime_series(index_vals, n):
    """True/False per date (None if not enough history). Missing index days carry forward."""
    vals, last = [], None
    for v in index_vals:
        last = v if v else last
        vals.append(last)
    clean = [v if v is not None else 0.0 for v in vals]
    ma = sma(clean, n)
    first = next((i for i, v in enumerate(vals) if v is not None), len(vals))
    reg = [None if (not ok(ma[i]) or i < first + n - 1) else vals[i] > ma[i] for i in range(len(vals))]
    return reg, vals, ma


def rank_on(inds, k, members):
    """Momentum rank (1 = strongest) among universe members on date index k."""
    scored = []
    for key in members:
        ind = inds.get(key)
        if not ind:
            continue
        i = ind.row(k)
        if i is not None and ok(ind.mom[i]):
            scored.append((ind.mom[i], key))
    scored.sort(reverse=True)
    return {key: r + 1 for r, (_, key) in enumerate(scored)}

def stop_distance(cfg, price, atr_value):
    """Initial stop distance in ₹: ATR-based (default) or a fixed % of price."""
    if cfg.get("stop_pct"):
        return price * cfg["stop_pct"] / 100
    return cfg["stop_atr"] * atr_value


def chart_stop_level(cfg, panel, ind, i):
    """Absolute stop level from the chart (or None if the mode isn't chart-based).
    Modes: low<N> (lowest low of the last N sessions incl. signal day), prev_week_low (low of the
    last completed calendar week), breakout_level (just below the old 52-week high)."""
    mode = cfg.get("stop_mode", "atr")
    s = ind.s
    buf = 1 - cfg.get("stop_buffer_pct", 0.5) / 100
    if mode.startswith("low"):
        n = int(mode[3:])
        return min(s.l[max(0, i - n + 1):i + 1]) * buf
    if mode == "prev_week_low":
        wk = panel.dates[s.di[i]].isocalendar()[:2]
        j, prev = i, None
        while j > 0:
            j -= 1
            w = panel.dates[s.di[j]].isocalendar()[:2]
            if w != wk:
                prev = w
                break
        if prev is None:
            return None
        lows = []
        while j >= 0 and panel.dates[s.di[j]].isocalendar()[:2] == prev:
            lows.append(s.l[j])
            j -= 1
        return min(lows) * buf
    if mode == "breakout_level":
        return ind.hi[i] * buf
    return None


def target_price(cfg, fill, stop_dist):
    """Fixed profit target, or None (let winners run): target_r x stop distance, or target_pct above entry."""
    if cfg.get("target_r"):
        return fill + cfg["target_r"] * stop_dist
    if cfg.get("target_pct"):
        return fill * (1 + cfg["target_pct"] / 100)
    return None



def plan_stop(cfg, panel, ind, i, limit_px):
    """(stop level or None, stop distance from the limit price, skip reason or None)."""
    level = chart_stop_level(cfg, panel, ind, i)
    dist = limit_px - level if level is not None else stop_distance(cfg, ind.s.c[i], ind.atr[i])
    pct = dist / limit_px * 100
    if pct > cfg.get("max_stop_pct", 100):
        return level, dist, "stop_too_wide"
    if pct < cfg.get("min_stop_pct", 0):
        dist = limit_px * cfg["min_stop_pct"] / 100
        level = limit_px - dist
    return level, dist, None

# ---------------------------------------------------------------- universe

def nifty500():
    if not N500_FILE.exists():
        return {}, {}
    syms, sector = set(), {}
    for r in csv.DictReader(io.StringIO(N500_FILE.read_text(encoding="utf-8", errors="replace"))):
        sym = (r.get("Symbol") or "").strip()
        isin = (r.get("ISIN Code") or "").strip()
        if sym:
            syms.add(isin or sym)
            sector[sym] = (r.get("Industry") or "").strip()
    return syms, sector

# ---------------------------------------------------------------- scan

def cmd_scan(args):
    cfg = load_config(args.config)
    if not args.no_update:
        update(quiet=True)
    members, sector = nifty500()
    if not members:
        sys.exit("NIFTY 500 list missing - run `scan.py update` (needs internet)")
    panel = Panel(start=dt.date.today() - dt.timedelta(days=470) if not args.date else
                  dt.date.fromisoformat(args.date) - dt.timedelta(days=470),
                  end=dt.date.fromisoformat(args.date) if args.date else None)
    if not panel.dates:
        sys.exit("no cached data - run `scan.py update`")
    k = len(panel.dates) - 1
    asof = panel.dates[k]
    keys = {key for key, s in panel.series.items() if key in members or s.sym in members}
    inds = {key: Ind(panel.series[key], cfg) for key in keys}
    ranks = rank_on(inds, k, keys)
    reg, ivals, ima = regime_series(panel.index, cfg["regime_sma"])
    regime_on = bool(reg[k]) if reg[k] is not None else None

    equity = args.equity
    risk_amt = equity * cfg["risk_pct"] / 100 if equity else None

    signals, shadow = [], []
    for key in keys:
        ind = inds[key]
        i = ind.row(k)
        if i is None:
            continue
        f, fail = evaluate(ind, i, ranks.get(key), cfg)
        f["sector"] = sector.get(ind.s.sym, "")
        recent_ca = [panel.dates[dk].isoformat() for _, _, dk in ind.s.ca if k - dk < 260]
        if recent_ca:
            f["corporate_action_adjusted"] = recent_ca
        if not fail:
            limit = tick_round(f["close"] * (1 + cfg["limit_buffer_pct"] / 100))
            level, dist, skip = plan_stop(cfg, panel, ind, i, limit)
            if skip:
                fail.append(skip)
        if not fail:
            f["limit_price"] = limit
            f["skip_if_open_above"] = tick_round(f["close"] * (1 + cfg["max_gap_pct"] / 100), up=True)
            f["stop_mode"] = cfg.get("stop_mode", "atr")
            if level is not None and f["stop_mode"] not in ("atr", "pct"):
                f["stop_level"] = tick_round(level)  # absolute: stop stays here whatever the fill
            f["stop_distance"] = round(dist, 2)      # from the limit price; for atr/pct: from the fill
            f["stop_est"] = tick_round(limit - dist)
            f["stop_pct"] = round(dist / limit * 100, 2)
            if cfg.get("target_r"):
                f["target_r"] = cfg["target_r"]
                f["target_est"] = tick_round(limit + cfg["target_r"] * dist, up=True)
            if risk_amt:
                qty = int(risk_amt // dist) if dist > 0 else 0
                cap = equity * cfg["max_position_pct"] / 100
                qty = min(qty, int(cap // limit))
                f["qty_est"], f["value_est"] = qty, round(qty * limit, 0)
            signals.append(f)
        near = (f.get("rank") or 999) <= cfg["rank_hold"] or "no_52w_breakout" not in fail
        if "history<1y" not in fail and near:
            shadow.append(dict(f, failed=";".join(fail), passed=not fail, regime_on=regime_on))
    signals.sort(key=lambda x: -x["score"])
    for n, sg in enumerate(signals, 1):
        sg["priority"] = n

    # open positions -> trailing stop + exit checks
    pos_out = []
    pos_file = Path(args.positions) if args.positions else HOME / "positions.json"
    positions = _jload(pos_file, {}).get("positions", []) if pos_file.exists() else []
    for p in positions:
        s = panel.by_sym.get(p["symbol"])
        row = {"symbol": p["symbol"], "qty": p.get("qty"), "entry_price": p.get("entry_price"),
               "current_stop": p.get("stop")}
        if not s or not len(s.di) or s.di[-1] != k:
            row["warning"] = "no data for this symbol on the scan date - check manually"
            pos_out.append(row)
            continue
        key = s.isin or s.sym
        ind = inds.get(key) or Ind(s, cfg)
        i = len(s.c) - 1
        ed = dt.date.fromisoformat(p["entry_date"])
        since = [s.c[j] for j in range(len(s.c)) if panel.dates[s.di[j]] >= ed]
        hc = max(since + [p.get("highest_close") or 0]) if since else p.get("highest_close")
        trail = (tick_round(hc - cfg["trail_atr"] * ind.atr[i])
                 if cfg.get("trail_atr", 0) > 0 and hc and ok(ind.atr[i]) else None)
        new_stop = max(x for x in (p.get("stop") or 0, trail or 0))
        rk = ranks.get(key)
        exits = []
        if cfg.get("exit_on_50dma") and ok(ind.sma_f[i]) and s.c[i] < ind.sma_f[i]:
            exits.append("close_below_50dma")
        if cfg.get("exit_on_rank") and (rk is None or rk > cfg["rank_hold"]):
            exits.append(f"rank_{rk}>{cfg['rank_hold']}" if rk else "not_ranked")
        held = sum(1 for j in range(len(s.c)) if panel.dates[s.di[j]] > ed)
        if cfg.get("max_hold_days") and held >= cfg["max_hold_days"]:
            exits.append("time_stop")
        stop_hit = target_hit = None
        if p.get("stop") and s.l[i] <= p["stop"]:
            exits.insert(0, "stop_touched")  # checked first: if both touched the same day, assume the stop
            stop_hit = round(min(s.o[i], p["stop"]), 2)  # gap below the stop fills at the open
        elif p.get("target") and s.h[i] >= p["target"]:
            exits.insert(0, "target_touched")
            target_hit = round(max(s.o[i], p["target"]), 2)  # gap above the target fills at the open
        row.update({"close": round(s.c[i], 2), "highest_close": round(hc, 2) if hc else None,
                    "atr": _r(ind.atr[i], 2), "rank": rk, "sma50": _r(ind.sma_f[i], 2),
                    "new_stop": new_stop, "raise_stop": bool(p.get("stop") and new_stop > p["stop"]),
                    "exit": exits[0] if exits else None, "exit_reasons": exits, "stop_hit_price": stop_hit,
                    "target": p.get("target"), "target_hit_price": target_hit, "sessions_held": held,
                    "day_low": round(s.l[i], 2), "day_open": round(s.o[i], 2),
                    "unrealized_pct": round((s.c[i] / p["entry_price"] - 1) * 100, 2) if p.get("entry_price") else None})
        pos_out.append(row)

    out = {
        "as_of": asof.isoformat(),
        "strategy_version": cfg["version"],
        "data_age_days": (dt.date.today() - asof).days,
        "regime": {"nifty500_close": ivals[k], "sma200": _r(ima[k], 2),
                   "new_entries_allowed": regime_on},
        "universe": len(keys), "ranked": len(ranks),
        "equity": equity, "risk_per_trade": risk_amt,
        "signals": signals if regime_on else [],
        "signals_blocked_by_regime": [] if regime_on else signals,
        "positions": pos_out,
        "rules": {k2: cfg.get(k2) for k2 in ("rank_entry", "vol_mult", "rsi_min", "rsi_max", "max_ext_pct",
                                              "stop_mode", "stop_buffer_pct", "min_stop_pct", "max_stop_pct",
                                              "target_r", "trail_atr", "risk_pct", "max_positions",
                                              "max_position_pct", "limit_buffer_pct", "max_gap_pct")},
    }
    if not args.no_shadow:
        _append_shadow(asof, shadow, cfg["version"])
    runs = HOME / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"scan_{asof.isoformat()}_{cfg['version']}.json"
    path.write_text(json.dumps(out, indent=1))
    out["saved_to"] = str(path)
    print(json.dumps(out, indent=1))

# ---------------------------------------------------------------- shadow book

SHADOW_COLS = ["date", "symbol", "sector", "version", "passed", "failed", "regime_on", "rank", "score", "close",
               "rsi", "vol_ratio", "ext_pct", "breakout_pct", "atr", "turnover_cr",
               "ret5", "ret10", "ret20", "ret60", "mfe20", "mae20"]


def _append_shadow(asof, rows, version):
    SHADOW.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if SHADOW.exists():
        with SHADOW.open() as fh:
            existing = {(r["date"], r["symbol"]) for r in csv.DictReader(fh)}
    new = not SHADOW.exists()
    with SHADOW.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SHADOW_COLS, extrasaction="ignore")
        if new:
            w.writeheader()
        for r in rows:
            if (asof.isoformat(), r["symbol"]) in existing:
                continue
            w.writerow(dict(r, date=asof.isoformat(), version=version))


def cmd_outcomes(args):
    if not SHADOW.exists():
        print(json.dumps({"updated": 0, "note": "shadow book is empty"}))
        return
    with SHADOW.open() as fh:
        rows = list(csv.DictReader(fh))
    todo = [r for r in rows if not r.get("ret60")]
    if not todo:
        print(json.dumps({"updated": 0}))
        return
    first = min(dt.date.fromisoformat(r["date"]) for r in todo)
    panel = Panel(start=first, symbols={r["symbol"] for r in todo})
    dpos = {d: i for i, d in enumerate(panel.dates)}
    n_upd = 0
    for r in todo:
        s = panel.by_sym.get(r["symbol"])
        k = dpos.get(dt.date.fromisoformat(r["date"]))
        i = s.row(k) if s and k is not None else None
        if i is None:
            continue
        base = s.c[i]
        for h in (5, 10, 20, 60):
            if i + h < len(s.c) and not r.get(f"ret{h}"):
                r[f"ret{h}"] = round((s.c[i + h] / base - 1) * 100, 2)
                n_upd += 1
        if i + 20 < len(s.c) and not r.get("mfe20"):
            r["mfe20"] = round((max(s.h[i + 1:i + 21]) / base - 1) * 100, 2)
            r["mae20"] = round((min(s.l[i + 1:i + 21]) / base - 1) * 100, 2)
    tmp = SHADOW.with_suffix(".tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SHADOW_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(SHADOW)
    print(json.dumps({"updated_fields": n_upd, "rows": len(rows)}))

# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update", help="download missing NSE days")
    u.add_argument("--from", dest="start", help="YYYY-MM-DD (default: ~15 months ago)")
    u.add_argument("--to", dest="end", help="YYYY-MM-DD (default: today)")
    s = sub.add_parser("scan", help="signals + position updates as JSON")
    s.add_argument("--equity", type=float, help="current account equity in ₹ (for position sizing)")
    s.add_argument("--positions", help="positions.json path (default: ~/ai-momentum-trader/positions.json)")
    s.add_argument("--config", help="config.json path (default: ~/ai-momentum-trader/config.json)")
    s.add_argument("--date", help="scan as of this past date (YYYY-MM-DD) instead of the latest")
    s.add_argument("--no-update", action="store_true", help="skip downloading new days")
    s.add_argument("--no-shadow", action="store_true", help="don't write to the shadow book")
    sub.add_parser("outcomes", help="fill forward returns into the shadow book")
    b = sub.add_parser("backtest", help="replay the rules over history")
    b.add_argument("--from", dest="start", default="2016-01-01")
    b.add_argument("--to", dest="end")
    b.add_argument("--config", help="config file to test (default: current)")
    b.add_argument("--capital", type=float, default=100000)
    b.add_argument("--split", default="2021-01-01", help="out-of-sample starts on this date")
    b.add_argument("--out", help="folder for trades.csv / equity.csv / summary.json")
    b.add_argument("--no-update", action="store_true")
    a = ap.parse_args()
    if a.cmd == "update":
        update(dt.date.fromisoformat(a.start) if a.start else None, dt.date.fromisoformat(a.end) if a.end else None)
    elif a.cmd == "scan":
        cmd_scan(a)
    elif a.cmd == "outcomes":
        cmd_outcomes(a)
    elif a.cmd == "backtest":
        from backtest import run_backtest  # sibling file
        run_backtest(a)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
