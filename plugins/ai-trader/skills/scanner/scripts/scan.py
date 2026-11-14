#!/usr/bin/env python3
"""AI Trader scanner: applies any strategy file to free NSE end-of-day data (Python standard library only).

Data: free, official NSE archive files (daily bhavcopy + index closes + index constituent lists).
No broker login, no API key. Everything is cached in ~/ai-trader/data.

Commands
  update                 download any missing days (first run: ~15 months, then 1 file/day)
  scan                   today's market filter, entry signals, and exit/stop updates for open positions (JSON)
  outcomes               fill forward returns (+5/+10/+20/+60 sessions) into the shadow book
  backtest               replay a strategy over history (see backtest --help)

The rules come from the strategy file (default: the active strategy in ~/ai-trader/config.json).
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402
from engine import NAN, Ctx, IndexData, ok, _nan  # noqa: E402,F401

HOME = engine.HOME
DATA = HOME / "data"
EQ_DIR = DATA / "eq"
INDEX_FILE = DATA / "index_nifty500.json"
HOLIDAYS_FILE = DATA / "no_data_days.json"
SHADOW = HOME / "learning" / "shadow_book.csv"

UA = "Mozilla/5.0 (compatible; ai-trader/2.0; daily NSE archive download)"
UDIFF_START = dt.date(2024, 7, 8)  # NSE switched the bhavcopy format on this date
KEEP_SERIES = {"EQ", "BE", "BZ"}
INDEX_NAMES = {"nifty 500", "cnx 500"}

# ---------------------------------------------------------------- utilities

def log(*a):
    print(*a, file=sys.stderr, flush=True)


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
    ensure_list("nifty500")
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
# ---------------------------------------------------------------- universes (NSE index constituent lists)

def list_file(name):
    return DATA / f"{name}_list.csv"


def ensure_list(name, max_age_days=7):
    """Download/refresh an index constituent list (weekly). Returns True if a list is available."""
    p = list_file(name)
    if not p.exists() or time.time() - p.stat().st_mtime > max_age_days * 86400:
        try:
            raw = fetch(f"https://nsearchives.nseindia.com/content/indices/{engine.UNIVERSES[name][0]}")
        except Exception as e:  # keep the old list if NSE is unreachable
            raw = None
            log(f"could not refresh the {name} list ({e})")
        if raw:
            DATA.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
    return p.exists()


def members_of(name):
    """(set of ISINs/symbols, {symbol: industry}) for a universe."""
    p = list_file(name)
    if not p.exists():
        return set(), {}
    keys, sector = set(), {}
    for r in csv.DictReader(io.StringIO(p.read_text(encoding="utf-8", errors="replace"))):
        sym = (r.get("Symbol") or "").strip()
        isin = (r.get("ISIN Code") or "").strip()
        if sym:
            keys.add(isin or sym)
            sector[sym] = (r.get("Industry") or "").strip()
    return keys, sector


def liquidity_score(s):
    """Fallback ranking when a strategy gives no score: 20-day average traded value."""
    return engine.sma(s.t, 20)


def assign_ranks(panel, results, members_for, days):
    """Rank (1 = highest score) among the universe members, for each date index in days.
    Sets res.rank on every result; returns {key: score array}."""
    scores = {key: (res.score if res.score is not None else liquidity_score(res.s)) for key, res in results.items()}
    ranks = {key: _nan(len(res.s.c)) for key, res in results.items()}
    for k in days:
        scored = []
        for key in members_for(k):
            res = results.get(key)
            if res is None:
                continue
            i = res.s.row(k)
            if i is not None and ok(scores[key][i]):
                scored.append((scores[key][i], key, i))
        scored.sort(reverse=True)
        for r, (_, key, i) in enumerate(scored, 1):
            ranks[key][i] = float(r)
    for key, res in results.items():
        res.rank = ranks[key]
    return scores


# descriptive features stored for every candidate in the shadow book (the same for every strategy, for learning)
FEATURES = {name: engine.parse(rule) for name, rule in {
    "rsi": "rsi(14)", "vol_ratio": "volume / avg_volume(50)", "ext_pct": "(close / ema(20) - 1) * 100",
    "breakout_pct": "(close / highest_high(252) - 1) * 100", "atr": "atr(14)", "turnover_cr": "sma(20, turnover_cr)",
    "sma50": "sma(50)", "sma200": "sma(200)", "ret126": "ret(126)",
}.items()}
_ROUND = {"rsi": 1, "vol_ratio": 2, "ext_pct": 1, "breakout_pct": 2, "atr": 2, "turnover_cr": 1, "sma50": 2,
          "sma200": 2, "ret126": 1}


def features(s, index, i):
    ctx = Ctx(s, {}, index)
    out = {}
    for name, node in FEATURES.items():
        v = ctx.vec(node)[i]
        out[name] = round(v, _ROUND[name]) if ok(v) else None
    return out


def evaluate(st, res, i):
    """(passed, failed rule names) for row i: universe filters, the script's checks / buy, and max_rank."""
    if res.score is not None and not ok(res.score[i]):
        return False, ["no_history"]
    fail = st.universe_checks(res.s, i)
    rank = res.rank[i] if res.rank is not None and ok(res.rank[i]) else None
    if st.max_rank is not None and (rank is None or rank > st.max_rank):
        fail.append("rank")
    fail += [name for name, col in res.checks.items() if not col[i]]
    if not res.buy[i] and not any(name in res.checks for name in fail):
        fail.append("buy_rule")
    return (res.buy[i] and not fail), fail


class Book:
    """Everything a strategy needs on one panel: the script's answers for each stock, with ranks."""

    def __init__(self, panel, st, index):
        self.st, self.index, self.panel = st, index, panel
        ensure_list(st.universe)
        members, self.sector = members_of(st.universe)
        if not members:
            sys.exit(f"{st.universe} constituents list missing - run `scan.py update` (needs internet)")
        self.keys = {key for key, s in panel.series.items() if key in members or s.sym in members}
        self.res = {key: st.run_series(panel.series[key], panel, index) for key in self.keys}
        self.scores = assign_ranks(panel, self.res, lambda k: self.keys, range(len(panel.dates)))

    def result_for(self, s):
        key = s.isin or s.sym
        if key not in self.res:  # held stock that left the universe: run the script on it, rank unknown
            r = self.st.run_series(s, self.panel, self.index)
            r.rank = _nan(len(s.c))
            self.res[key] = r
        return self.res[key]


def regime_at(st, index, k):
    """True/False/None (not enough history) for the strategy's market filter; True if it has none."""
    if st.regime is None:
        return True
    v = Ctx(None, {}, index).vec(st.regime)[k]
    return bool(v) if ok(v) else None

# ---------------------------------------------------------------- scan


def cmd_scan(args):
    st = engine.load(args.strategy)
    if not args.no_update:
        update(quiet=True)
    end = dt.date.fromisoformat(args.date) if args.date else None
    days_back = max(470, int(st.history * 1.5) + 60)
    panel = Panel(start=(end or dt.date.today()) - dt.timedelta(days=days_back), end=end)
    if not panel.dates:
        sys.exit("no cached data - run `scan.py update`")
    k = len(panel.dates) - 1
    asof = panel.dates[k]
    index = IndexData(panel.index)
    books = {st.id: Book(panel, st, index)}
    book = books[st.id]
    regime_on = regime_at(st, index, k)
    equity = args.equity

    signals, shadow = [], []
    for key, res in book.res.items():
        s = res.s
        i = s.row(k)
        if i is None:
            continue
        passed, fail = evaluate(st, res, i)
        rank = res.rank[i] if ok(res.rank[i]) else None
        score = book.scores[key][i]
        f = {"symbol": s.sym, "close": round(s.c[i], 2), "rank": int(rank) if rank else None,
             "score": round(score, 4) if ok(score) else None, "sector": book.sector.get(s.sym, "")}
        if passed or (fail != ["no_history"] and len(fail) <= 2):
            f.update(features(s, index, i))
        recent_ca = [panel.dates[dk].isoformat() for _, _, dk in s.ca if k - dk < 260]
        if recent_ca:
            f["corporate_action_adjusted"] = recent_ca
        if passed:
            limit = tick_round(f["close"] * (1 + st.limit_buffer_pct / 100))
            level, dist, skip = st.plan_stop(res, i, limit)
            if skip:
                passed = False
                fail.append(skip)
        if passed:
            f["limit_price"] = limit
            f["skip_if_open_above"] = tick_round(f["close"] * (1 + st.max_gap_pct / 100), up=True)
            f["stop_type"] = st.stop["type"] if st.stop else None
            if st.stop:
                if level is not None and st.stop["type"] == "level":
                    f["stop_level"] = tick_round(level)  # absolute: the stop stays here whatever the fill
                f["stop_distance"] = round(dist, 2)      # pct/atr: stop = fill - stop_distance
                f["stop_est"] = tick_round(limit - dist)
                f["stop_pct"] = round(dist / limit * 100, 2)
            if st.target:
                f["target_type"], f["target_value"] = st.target["type"], st.target["value"]
                tgt = st.target_price(limit, limit - dist if st.stop else None)
                f["target_est"] = tick_round(tgt, up=True) if tgt else None
            if st.trail:
                f["trail"] = st.data.get("trail")
            if equity and st.risk:
                qty = st.size(equity, limit, dist)
                f["qty_est"], f["value_est"] = qty, round(qty * limit, 0)
            signals.append(f)
        if fail != ["no_history"] and len(fail) <= 2:
            shadow.append(dict(f, failed=";".join(fail), passed=not fail, regime_on=regime_on))
    signals.sort(key=lambda x: (-(x["score"] if x["score"] is not None else -1e18), x["symbol"]))
    pos_file = Path(args.positions) if args.positions else HOME / "positions.json"
    positions = engine.read_json(pos_file, {}).get("positions", []) if pos_file.exists() else []
    sectors = engine.sector_map() if st.has_hooks else {}

    def pos_info(p, s=None, i=None):
        px = s.c[i] if s is not None else (p.get("last_price") or p.get("entry_price"))
        ed = dt.date.fromisoformat(p["entry_date"])
        held = sum(1 for j in range(len(s.c)) if panel.dates[s.di[j]] > ed) if s is not None else None
        return {"symbol": p["symbol"], "sector": sectors.get(p["symbol"], ""), "entry_date": ed,
                "entry_price": p.get("entry_price"), "qty": p.get("qty"), "stop": p.get("stop"), "target": p.get("target"),
                "highest_close": p.get("highest_close"), "days_held": held, "close": px,
                "unrealized_pct": round((px / p["entry_price"] - 1) * 100, 2) if p.get("entry_price") and px else None}

    blocked_by_rule = []
    if st._allow and signals:  # the strategy's own portfolio-level entry rules, in priority order
        trades_csv = HOME / "journal" / "trades.csv"
        recent = []
        if trades_csv.exists():
            with trades_csv.open() as fh:
                recent = list(csv.DictReader(fh))[-50:]
        book = {"date": asof, "equity": equity, "cash": None,
                "positions": [pos_info(p) for p in positions], "recent_trades": recent}
        kept = []
        for sg in signals:
            cand = {"symbol": sg["symbol"], "sector": sectors.get(sg["symbol"], ""), "date": asof, "close": sg["close"],
                    "score": sg["score"], "rank": sg["rank"], "stop_pct": sg.get("stop_pct")}
            why = st.allow(cand, book)
            if why:
                blocked_by_rule.append(dict(sg, blocked_reason=why))
            else:
                kept.append(sg)
                book["positions"].append({"symbol": sg["symbol"], "sector": cand["sector"], "pending": True})
        signals = kept
    for n, sg in enumerate(signals, 1):
        sg["priority"] = n

    # open positions -> each is managed by the strategy that opened it
    pos_out = []
    for p in positions:
        sid = p.get("strategy") or engine.LEGACY_ID
        row = {"symbol": p["symbol"], "strategy": sid, "qty": p.get("qty"), "entry_price": p.get("entry_price"),
               "current_stop": p.get("stop")}
        try:
            if sid not in books:
                books[sid] = Book(panel, engine.load(sid), index)
        except (engine.StrategyError, SystemExit) as e:
            row["warning"] = f"can't load strategy '{sid}' ({e}) - stop/target GTT still applies; check manually"
            pos_out.append(row)
            continue
        pb = books[sid]
        pst = pb.st
        s = panel.by_sym.get(p["symbol"])
        if not s or not len(s.di) or s.di[-1] != k:
            row["warning"] = "no data for this symbol on the scan date - check manually"
            pos_out.append(row)
            continue
        try:
            res = pb.result_for(s)
        except engine.StrategyError as e:
            row["warning"] = f"{e} - stop/target GTT still applies; check manually"
            pos_out.append(row)
            continue
        i = len(s.c) - 1
        ed = dt.date.fromisoformat(p["entry_date"])
        since = [s.c[j] for j in range(len(s.c)) if panel.dates[s.di[j]] >= ed]
        hc = max(since + [p.get("highest_close") or 0]) if since else p.get("highest_close")
        cur = p.get("stop")
        trail = pst.trail_level(res, i, hc) if hc else None
        new_stop = cur
        if trail is not None:
            trail = tick_round(trail)
            only_up = (pst.risk or {}).get("stops_only_up", True)
            new_stop = max(cur or 0, trail) if only_up else trail
        exits = []
        if res.sell is not None and res.sell[i]:
            exits.append(res.sell[i] if isinstance(res.sell[i], str) else "sell_rule")
        held = sum(1 for j in range(len(s.c)) if panel.dates[s.di[j]] > ed)
        if pst.max_hold_days and held >= pst.max_hold_days:
            exits.append("time_stop")
        if pst._manage:  # the strategy's own rules for an open position
            if not sectors:
                sectors.update(engine.sector_map())
            try:
                act = pst.manage_position(dict(pos_info(p, s, i), highest_close=hc), engine.Bars.from_series(s, panel.dates))
            except engine.StrategyError as e:
                act = {}
                row["warning"] = f"{e} - stop/target GTT still applies"
            if "stop" in act:
                only_up = (pst.risk or {}).get("stops_only_up", True)
                ns = tick_round(act["stop"])
                new_stop = max(new_stop or 0, ns) if only_up else ns
            if "sell" in act:
                exits.append(act["sell"])
        stop_hit = target_hit = None
        if cur and s.l[i] <= cur:
            exits.insert(0, "stop_touched")  # checked first: if both touched the same day, assume the stop
            stop_hit = round(min(s.o[i], cur), 2)  # a gap below the stop fills at the open
        elif p.get("target") and s.h[i] >= p["target"]:
            exits.insert(0, "target_touched")
            target_hit = round(max(s.o[i], p["target"]), 2)  # a gap above the target fills at the open
        rk = res.rank[i] if res.rank is not None and ok(res.rank[i]) else None
        row.update({"close": round(s.c[i], 2), "highest_close": round(hc, 2) if hc else None,
                    "rank": int(rk) if rk else None, "new_stop": new_stop,
                    "raise_stop": bool(new_stop is not None and cur is not None and new_stop > cur),
                    "lower_stop": bool(new_stop is not None and cur is not None and new_stop < cur),
                    "exit": exits[0] if exits else None, "exit_reasons": exits, "stop_hit_price": stop_hit,
                    "target": p.get("target"), "target_hit_price": target_hit, "sessions_held": held,
                    "day_low": round(s.l[i], 2), "day_open": round(s.o[i], 2),
                    "unrealized_pct": round((s.c[i] / p["entry_price"] - 1) * 100, 2) if p.get("entry_price") else None})
        pos_out.append(row)

    out = {
        "as_of": asof.isoformat(),
        "strategy": st.id, "strategy_name": st.name, "strategy_version": st.version,
        "risk_confirmed": st.risk_confirmed,
        "data_age_days": (dt.date.today() - asof).days,
        "regime": {"rule": (st.risk or {}).get("regime"), "nifty500_close": index.close[k] if ok(index.close[k]) else None,
                   "new_entries_allowed": regime_on},
        "universe": st.universe, "universe_size": len(book.keys),
        "equity": equity, "sizing": (st.risk or {}).get("sizing"),
        "signals": signals if regime_on else [],
        "signals_blocked_by_regime": [] if regime_on else signals,
        "signals_blocked_by_strategy_rule": blocked_by_rule,
        "positions": pos_out,
    }
    if not st.risk_confirmed:
        out["warning"] = ("this strategy's risk settings are not confirmed by the user: do not trade it. "
                          "Run the strategies skill to set them.")
    if not args.no_shadow:
        _append_shadow(asof, shadow, st)
    runs = HOME / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"scan_{asof.isoformat()}_{st.id}_{st.version}.json"
    path.write_text(json.dumps(out, indent=1))
    out["saved_to"] = str(path)
    print(json.dumps(out, indent=1))

# ---------------------------------------------------------------- shadow book

SHADOW_COLS = ["date", "strategy", "symbol", "sector", "version", "passed", "failed", "regime_on", "rank", "score",
               "close", "rsi", "vol_ratio", "ext_pct", "breakout_pct", "atr", "turnover_cr", "ret126",
               "ret5", "ret10", "ret20", "ret60", "mfe20", "mae20"]


def _read_shadow():
    if not SHADOW.exists():
        return []
    with SHADOW.open() as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r.setdefault("strategy", engine.LEGACY_ID)
        r["strategy"] = r["strategy"] or engine.LEGACY_ID
    return rows


def _write_shadow(rows):
    SHADOW.parent.mkdir(parents=True, exist_ok=True)
    tmp = SHADOW.with_suffix(".tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SHADOW_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(SHADOW)


def _append_shadow(asof, new_rows, st):
    rows = _read_shadow()
    have = {(r["date"], r["strategy"], r["symbol"]) for r in rows}
    d = asof.isoformat()
    rows += [dict(r, date=d, strategy=st.id, version=st.version) for r in new_rows
             if (d, st.id, r["symbol"]) not in have]
    _write_shadow(rows)


def cmd_outcomes(args):
    rows = _read_shadow()
    if not rows:
        print(json.dumps({"updated": 0, "note": "shadow book is empty"}))
        return
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
    _write_shadow(rows)
    print(json.dumps({"updated_fields": n_upd, "rows": len(rows)}))

# ---------------------------------------------------------------- CLI


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update", help="download missing NSE days")
    u.add_argument("--from", dest="start", help="YYYY-MM-DD (default: ~15 months ago)")
    u.add_argument("--to", dest="end", help="YYYY-MM-DD (default: today)")
    s = sub.add_parser("scan", help="signals + position updates as JSON")
    s.add_argument("--strategy", help="strategy id or .json path (default: the active strategy)")
    s.add_argument("--equity", type=float, help="current bot equity in ₹ (for position sizing)")
    s.add_argument("--positions", help="positions.json path (default: ~/ai-trader/positions.json)")
    s.add_argument("--date", help="scan as of this past date (YYYY-MM-DD) instead of the latest")
    s.add_argument("--no-update", action="store_true", help="skip downloading new days")
    s.add_argument("--no-shadow", action="store_true", help="don't write to the shadow book")
    sub.add_parser("outcomes", help="fill forward returns into the shadow book")
    b = sub.add_parser("backtest", help="replay a strategy over history")
    b.add_argument("--strategy", help="strategy id or .json path (default: the active strategy)")
    b.add_argument("--from", dest="start", default="2016-01-01")
    b.add_argument("--to", dest="end")
    b.add_argument("--capital", type=float, default=100000)
    b.add_argument("--split", default="2021-01-01", help="out-of-sample starts on this date")
    b.add_argument("--out", help="folder for trades.csv / equity.csv / summary.json")
    b.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="override one setting for this run, e.g. params.rsi_max=75 or risk.sizing.pct=1 "
                        "(value is JSON: numbers, true/false, null, \"text\")")
    b.add_argument("--sweep", metavar="KEY=V1,V2,...",
                   help="run once per value of one setting on the same data, e.g. risk.sizing.pct=1,1.5,2")
    b.add_argument("--no-limits", action="store_true", help="ignore the weekly limit and drawdown halt")
    b.add_argument("--no-update", action="store_true")
    a = ap.parse_args()
    if a.cmd == "update":
        update(dt.date.fromisoformat(a.start) if a.start else None, dt.date.fromisoformat(a.end) if a.end else None)
    elif a.cmd == "scan":
        try:
            cmd_scan(a)
        except engine.StrategyError as e:
            sys.exit(f"strategy problem: {e}")
    elif a.cmd == "outcomes":
        cmd_outcomes(a)
    elif a.cmd == "backtest":
        from backtest import run_backtest  # sibling file
        try:
            run_backtest(a)
        except engine.StrategyError as e:
            sys.exit(f"strategy problem: {e}")


if __name__ == "__main__":
    main()
