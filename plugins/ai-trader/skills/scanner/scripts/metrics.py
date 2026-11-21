"""Performance metrics for AI Trader backtests (Python standard library only).

Everything here is computed from two things a backtest writes: the daily equity curve (with NIFTY 500 beside
it) and the list of closed trades. backtest.py stores the results in summary.json; report.py draws them.

  perf(dates, values, rf_pct)          return / risk / risk-adjusted / drawdown numbers for one curve
  relative(dates, strat, bench, rf)    beta, alpha, correlation ... of the strategy against NIFTY 500
  trade_stats(trades, years)           win rate, profit factor, expectancy, largest win/loss ...
  distribution(trades)                 histogram + spread of trade returns (% and R)
  streaks(trades, monthly)             winning / losing streaks of trades and of months
  time_patterns(trades, monthly)       results by weekday, month of year, holding period, entry year
"""
import datetime as dt
import math

TRADING_DAYS = 252
RISK_FREE_PCT = 6.0   # yearly; roughly the average Indian 91-day T-bill yield over 2016-2026
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
HOLD_BUCKETS = [(0, 5), (6, 10), (11, 20), (21, 40), (41, 60), (61, 120), (121, None)]


def _d(x):
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def _r(x, n=2):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(x, n)


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _std(xs):
    if len(xs) < 2:
        return None
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _pctile(xs, q):
    """Percentile (0-100) with linear interpolation; xs need not be sorted."""
    if not xs:
        return None
    s = sorted(xs)
    pos = (len(s) - 1) * q / 100
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _skew(xs):
    sd = _std(xs)
    if not sd or len(xs) < 3:
        return None
    m = _mean(xs)
    n = len(xs)
    return n / ((n - 1) * (n - 2)) * sum(((x - m) / sd) ** 3 for x in xs)


def _clean(dates, values):
    return [(_d(d), v) for d, v in zip(dates, values) if v is not None and v > 0]


def monthly_returns(dates, values):
    """{'YYYY-MM': return} of a curve, each month vs the previous month's last value."""
    last = {}
    for d, v in _clean(dates, values):
        last[d.strftime("%Y-%m")] = v
    out, prev = {}, None
    first = next((v for _, v in _clean(dates, values)), None)
    for m in sorted(last):
        base = prev if prev is not None else first
        out[m] = last[m] / base - 1
        prev = last[m]
    return out


def _yearly(dates, values):
    last = {}
    for d, v in _clean(dates, values):
        last[d.year] = v
    out, prev = {}, None
    first = next((v for _, v in _clean(dates, values)), None)
    for y in sorted(last):
        out[y] = last[y] / (prev if prev is not None else first) - 1
        prev = last[y]
    return out


def _drawdowns(pts):
    """Underwater periods: (peak date, trough date, recovery date or None, depth)."""
    out, peak_d, peak_v = [], pts[0][0], pts[0][1]
    trough_d, depth = None, 0.0
    for d, v in pts[1:]:
        if v >= peak_v:
            if depth < 0:
                out.append((peak_d, trough_d, d, depth))
            peak_d, peak_v, trough_d, depth = d, v, None, 0.0
        else:
            dd = v / peak_v - 1
            if dd < depth:
                depth, trough_d = dd, d
    if depth < 0:
        out.append((peak_d, trough_d, None, depth))
    return out


def perf(dates, values, rf_pct=RISK_FREE_PCT):
    """Return, risk and risk-adjusted numbers of one daily value curve (strategy or index)."""
    pts = _clean(dates, values)
    if len(pts) < 3:
        return {}
    yrs = (pts[-1][0] - pts[0][0]).days / 365.25
    rets = [pts[i][1] / pts[i - 1][1] - 1 for i in range(1, len(pts))]
    rf_d = (1 + rf_pct / 100) ** (1 / TRADING_DAYS) - 1
    ex = [r - rf_d for r in rets]
    sd = _std(rets)
    down = math.sqrt(sum(min(0.0, x) ** 2 for x in ex) / len(ex))
    cagr = (pts[-1][1] / pts[0][1]) ** (1 / yrs) - 1 if yrs > 0 else None
    dds = _drawdowns(pts)
    worst = min(dds, key=lambda x: x[3]) if dds else None
    end = pts[-1][0]
    longest = max(dds, key=lambda x: ((x[2] or end) - x[0]).days) if dds else None
    peak, sq = pts[0][1], 0.0
    for _, v in pts:
        peak = max(peak, v)
        sq += (v / peak - 1) ** 2
    mret = monthly_returns(dates, values)
    yret = _yearly(dates, values)
    mdd = worst[3] if worst else 0.0
    gains = sum(x for x in rets if x > 0)
    losses = -sum(x for x in rets if x < 0)
    out = {
        "total_return_pct": _r((pts[-1][1] / pts[0][1] - 1) * 100, 1),
        "cagr_pct": _r(cagr * 100) if cagr is not None else None,
        "volatility_pct": _r(sd * math.sqrt(TRADING_DAYS) * 100) if sd else None,
        "downside_volatility_pct": _r(down * math.sqrt(TRADING_DAYS) * 100),
        "sharpe": _r(_mean(ex) / sd * math.sqrt(TRADING_DAYS)) if sd else None,
        "sortino": _r(_mean(ex) / down * math.sqrt(TRADING_DAYS)) if down else None,
        "calmar": _r(cagr / -mdd) if cagr is not None and mdd < 0 else None,
        "omega": _r(gains / losses) if losses else None,
        "max_drawdown_pct": _r(mdd * 100),
        "max_drawdown_peak": worst[0].isoformat() if worst else None,
        "max_drawdown_trough": worst[1].isoformat() if worst else None,
        "max_drawdown_recovered": worst[2].isoformat() if worst and worst[2] else None,
        "max_drawdown_recovery_days": (worst[2] - worst[1]).days if worst and worst[2] else None,
        "longest_underwater_days": ((longest[2] or end) - longest[0]).days if longest else 0,
        "longest_underwater_from": longest[0].isoformat() if longest else None,
        "longest_underwater_to": (longest[2].isoformat() if longest[2] else None) if longest else None,
        "ulcer_index": _r(math.sqrt(sq / len(pts)) * 100),
        "drawdowns_over_10pct": sum(1 for x in dds if x[3] <= -0.10),
        "var_95_daily_pct": _r(_pctile(rets, 5) * 100),
        "best_day_pct": _r(max(rets) * 100),
        "worst_day_pct": _r(min(rets) * 100),
        "positive_days_pct": _r(100 * sum(1 for r in rets if r > 0) / len(rets), 1),
        "best_month_pct": _r(max(mret.values()) * 100) if mret else None,
        "worst_month_pct": _r(min(mret.values()) * 100) if mret else None,
        "positive_months_pct": _r(100 * sum(1 for r in mret.values() if r > 0) / len(mret), 1) if mret else None,
        "avg_month_pct": _r(_mean(list(mret.values())) * 100) if mret else None,
        "best_year_pct": _r(max(yret.values()) * 100, 1) if yret else None,
        "worst_year_pct": _r(min(yret.values()) * 100, 1) if yret else None,
        "positive_years": sum(1 for r in yret.values() if r > 0),
        "years_counted": len(yret),
    }
    return out


def relative(dates, strat, bench, rf_pct=RISK_FREE_PCT):
    """The strategy's daily returns measured against NIFTY 500 on the same days."""
    pairs = [(s, b) for s, b in zip(strat, bench)]
    rs, rb = [], []
    for i in range(1, len(pairs)):
        (s0, b0), (s1, b1) = pairs[i - 1], pairs[i]
        if s0 and s1 and b0 and b1:
            rs.append(s1 / s0 - 1)
            rb.append(b1 / b0 - 1)
    if len(rs) < 20:
        return {}
    ms, mb = _mean(rs), _mean(rb)
    cov = sum((a - ms) * (b - mb) for a, b in zip(rs, rb)) / (len(rs) - 1)
    vb = _std(rb) ** 2
    ss = _std(rs)
    beta = cov / vb if vb else None
    rf_d = (1 + rf_pct / 100) ** (1 / TRADING_DAYS) - 1
    diff = [a - b for a, b in zip(rs, rb)]
    te = _std(diff)
    up = [(a, b) for a, b in zip(rs, rb) if b > 0]
    dn = [(a, b) for a, b in zip(rs, rb) if b < 0]
    return {
        "beta": _r(beta),
        "alpha_pct": _r(((ms - rf_d) - beta * (mb - rf_d)) * TRADING_DAYS * 100) if beta is not None else None,
        "correlation": _r(cov / (ss * math.sqrt(vb))) if ss and vb else None,
        "tracking_error_pct": _r(te * math.sqrt(TRADING_DAYS) * 100) if te else None,
        "information_ratio": _r(_mean(diff) / te * math.sqrt(TRADING_DAYS)) if te else None,
        "up_capture_pct": _r(100 * _mean([a for a, _ in up]) / _mean([b for _, b in up]), 1) if up else None,
        "down_capture_pct": _r(100 * _mean([a for a, _ in dn]) / _mean([b for _, b in dn]), 1) if dn else None,
        "days_beating_index_pct": _r(100 * sum(1 for d in diff if d > 0) / len(diff), 1),
    }


def trade_stats(trades, years=None):
    """Numbers about the closed trades themselves."""
    if not trades:
        return {"trades": 0}
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gp = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in losses)
    rets = [t["ret_pct"] for t in trades]
    rs = [t["r"] for t in trades if t.get("r") is not None]
    big_w = max(trades, key=lambda t: t["pnl"])
    big_l = min(trades, key=lambda t: t["pnl"])
    avg_w = gp / len(wins) if wins else None
    avg_l = gl / len(losses) if losses else None
    charges = [t["charges"] for t in trades if t.get("charges") is not None]
    return {
        "trades": len(trades), "winners": len(wins), "losers": len(losses),
        "win_rate_pct": _r(100 * len(wins) / len(trades), 1),
        "net_pnl": _r(gp - gl, 0), "gross_profit": _r(gp, 0), "gross_loss": _r(-gl, 0),
        "profit_factor": _r(gp / gl) if gl else None,
        "expectancy": _r((gp - gl) / len(trades), 0),
        "expectancy_pct": _r(_mean(rets)),
        "median_return_pct": _r(_pctile(rets, 50)),
        "avg_win": _r(avg_w, 0), "avg_loss": _r(-avg_l, 0) if avg_l is not None else None,
        "avg_win_pct": _r(_mean([t["ret_pct"] for t in wins])) if wins else None,
        "avg_loss_pct": _r(_mean([t["ret_pct"] for t in losses])) if losses else None,
        "payoff_ratio": _r(avg_w / avg_l) if avg_w and avg_l else None,
        "avg_r": _r(_mean(rs)) if rs else None,
        "largest_win": {"symbol": big_w["symbol"], "pnl": _r(big_w["pnl"], 0), "ret_pct": big_w["ret_pct"],
                        "exit_date": big_w["exit_date"]},
        "largest_loss": {"symbol": big_l["symbol"], "pnl": _r(big_l["pnl"], 0), "ret_pct": big_l["ret_pct"],
                         "exit_date": big_l["exit_date"]},
        "avg_hold_days": _r(_mean([t["hold_days"] for t in trades]), 1),
        "avg_hold_days_winners": _r(_mean([t["hold_days"] for t in wins]), 1) if wins else None,
        "avg_hold_days_losers": _r(_mean([t["hold_days"] for t in losses]), 1) if losses else None,
        "trades_per_year": _r(len(trades) / years, 1) if years else None,
        "total_charges": _r(sum(charges), 0) if charges else None,
    }


def _histogram(xs, target_bins=18):
    """Bins with a 'nice' width between the 2nd and 98th percentile; the tails go into the end bins."""
    if not xs:
        return {"width": None, "bins": []}
    lo, hi = _pctile(xs, 2), _pctile(xs, 98)
    if hi - lo < 1e-9:
        lo, hi = min(xs) - 1, max(xs) + 1
    raw = (hi - lo) / target_bins
    mag = 10 ** math.floor(math.log10(raw))
    width = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / width) * width
    stop = math.ceil(hi / width) * width
    n = max(1, int(round((stop - start) / width)))
    counts = [0] * n
    for x in xs:
        k = int(math.floor((x - start) / width))
        counts[min(max(k, 0), n - 1)] += 1
    bins = []
    for k in range(n):
        a, b = start + k * width, start + (k + 1) * width
        bins.append({"from": _r(a, 3), "to": _r(b, 3), "count": counts[k],
                     "open_low": k == 0 and min(xs) < a, "open_high": k == n - 1 and max(xs) >= b})
    return {"width": width, "bins": bins}


def _spread(xs):
    if not xs:
        return {}
    return {"count": len(xs), "mean": _r(_mean(xs)), "median": _r(_pctile(xs, 50)), "std": _r(_std(xs)),
            "skew": _r(_skew(xs)), "p5": _r(_pctile(xs, 5)), "p25": _r(_pctile(xs, 25)),
            "p75": _r(_pctile(xs, 75)), "p95": _r(_pctile(xs, 95)), "min": _r(min(xs)), "max": _r(max(xs))}


def distribution(trades):
    rets = [t["ret_pct"] for t in trades]
    rs = [t["r"] for t in trades if t.get("r") is not None]
    out = {"return_pct": dict(_histogram(rets), stats=_spread(rets))}
    if rs:
        out["r_multiple"] = dict(_histogram(rs), stats=_spread(rs))
    return out


def _runs(seq):
    """seq: [(is_win, item)] -> list of runs [win, [items]]."""
    runs = []
    for w, it in seq:
        if runs and runs[-1][0] == w:
            runs[-1][1].append(it)
        else:
            runs.append([w, [it]])
    return runs


def streaks(trades, monthly=None):
    """Winning / losing streaks of trades in the order they closed (and of months, from the equity curve)."""
    order = sorted(trades, key=lambda t: (t["exit_date"], t["entry_date"], t["symbol"]))
    runs = _runs([(t["pnl"] > 0, t) for t in order])

    def info(run):
        w, ts = run
        return {"length": len(ts), "from": ts[0]["exit_date"], "to": ts[-1]["exit_date"],
                "pnl": _r(sum(t["pnl"] for t in ts), 0), "symbols": [t["symbol"] for t in ts][:12]}
    out = {"trades": {}}
    for kind, flag in (("win", True), ("loss", False)):
        rr = [r for r in runs if r[0] is flag]
        freq = {}
        for r in rr:
            freq[len(r[1])] = freq.get(len(r[1]), 0) + 1
        top = sorted(rr, key=lambda r: (-len(r[1]), r[1][0]["exit_date"]))[:5]
        out["trades"][kind] = {
            "max": len(top[0][1]) if top else 0,
            "avg": _r(_mean([len(r[1]) for r in rr]), 1) if rr else None,
            "count": len(rr),
            "lengths": {str(k): v for k, v in sorted(freq.items())},
            "top": [info(r) for r in top],
        }
    if runs:
        out["trades"]["current"] = {"kind": "win" if runs[-1][0] else "loss", **info(runs[-1])}
    if monthly:
        mruns = _runs([(r > 0, (m, r)) for m, r in sorted(monthly.items())])
        for kind, flag in (("win", True), ("loss", False)):
            rr = [r for r in mruns if r[0] is flag]
            best = max(rr, key=lambda r: len(r[1])) if rr else None
            tot = 1.0
            for _, x in (best[1] if best else []):
                tot *= 1 + x
            out.setdefault("months", {})[kind] = {
                "max": len(best[1]) if best else 0,
                "from": best[1][0][0] if best else None, "to": best[1][-1][0] if best else None,
                "return_pct": _r((tot - 1) * 100, 1) if best else None}
    return out


def _group(trades, keyf, order):
    rows = []
    for key, label in order:
        ts = [t for t in trades if keyf(t) == key]
        w = [t for t in ts if t["pnl"] > 0]
        rows.append({"label": label, "trades": len(ts),
                     "win_rate_pct": _r(100 * len(w) / len(ts), 1) if ts else None,
                     "avg_return_pct": _r(_mean([t["ret_pct"] for t in ts])) if ts else None,
                     "pnl": _r(sum(t["pnl"] for t in ts), 0)})
    return rows


def _bucket(days):
    for k, (a, b) in enumerate(HOLD_BUCKETS):
        if days >= a and (b is None or days <= b):
            return k
    return 0


def time_patterns(trades, monthly=None):
    """Results grouped by when trades were bought and how long they were held."""
    out = {
        "weekday": _group(trades, lambda t: _d(t["entry_date"]).weekday(), list(enumerate(WEEKDAYS))),
        "entry_month": _group(trades, lambda t: _d(t["entry_date"]).month, [(i + 1, m) for i, m in enumerate(MONTHS)]),
        "hold_period": _group(trades, lambda t: _bucket(t["hold_days"]),
                              [(k, ("up to 5 days" if a == 0 else f"{a}–{b} days") if b else f"{a}+ days")
                               for k, (a, b) in enumerate(HOLD_BUCKETS)]),
    }
    years = sorted({_d(t["entry_date"]).year for t in trades})
    out["entry_year"] = _group(trades, lambda t: _d(t["entry_date"]).year, [(y, str(y)) for y in years])
    if monthly:
        rows = []
        for i, m in enumerate(MONTHS):
            xs = [r for k, r in monthly.items() if int(k[5:7]) == i + 1]
            rows.append({"label": m, "years": len(xs),
                         "avg_return_pct": _r(_mean(xs) * 100) if xs else None,
                         "positive_pct": _r(100 * sum(1 for x in xs if x > 0) / len(xs), 0) if xs else None,
                         "best_pct": _r(max(xs) * 100) if xs else None, "worst_pct": _r(min(xs) * 100) if xs else None})
        out["calendar_month"] = rows
    return out


def analysis(dates, equity, trades):
    """Everything the report's Trade analysis tab shows, in one dict."""
    monthly = monthly_returns(dates, equity)
    return {"distribution": distribution(trades), "streaks": streaks(trades, monthly),
            "time_patterns": time_patterns(trades, monthly)}
