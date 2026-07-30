"""
indicators.py — deterministic technical indicators + trade-timing analysis.
---------------------------------------------------------------------------
Turns the candle series (from market_context.get_candles) into the numeric
facts that let a report say things like:

  "At entry, RSI(14) was 71 — overbought — and price was 2.1% above its
   20-period EMA."
  "You exited at 1,768. Over the next 8 hours price fell a further 1.4% to
   1,742, so the exit captured 74% of the move that was available."

EVERY number here is COMPUTED from real candles. Nothing is recalled by an
LLM. If candles are missing, the fields are simply absent and the report says
nothing about them — it never guesses.

This module has no I/O and no LLM. It takes candles in, returns numbers out,
so it is fully unit-testable and deterministic.

Indicators implemented (standard formulas):
  SMA, EMA, RSI(14) (Wilder), MACD(12,26,9), Bollinger(20,2), ATR(14),
  recent swing high/low. All from OHLC candles: [{time,open,high,low,close}].
"""
from __future__ import annotations
from datetime import datetime, timedelta

FMT = "%Y-%m-%d %H:%M:%S"


def _dt(s):
    return datetime.strptime(s, FMT) if isinstance(s, str) else s


def _closes(candles):
    return [c["close"] for c in candles]


def sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def ema_series(vals, n):
    if len(vals) < n:
        return []
    k = 2 / (n + 1)
    out = [sum(vals[:n]) / n]
    for v in vals[n:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def ema(vals, n):
    s = ema_series(vals, n)
    return s[-1] if s else None


def rsi(vals, n=14):
    if len(vals) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(vals)):
        d = vals[i] - vals[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for i in range(n, len(gains)):          # Wilder smoothing
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 1)


def macd(vals, fast=12, slow=26, signal=9):
    if len(vals) < slow + signal:
        return None
    ef, es = ema_series(vals, fast), ema_series(vals, slow)
    n = min(len(ef), len(es))
    macd_line = [ef[-n + i] - es[-n + i] for i in range(n)]
    sig = ema_series(macd_line, signal)
    if not sig:
        return None
    return {"macd": round(macd_line[-1], 4), "signal": round(sig[-1], 4),
            "hist": round(macd_line[-1] - sig[-1], 4)}


def bollinger(vals, n=20, k=2):
    if len(vals) < n:
        return None
    window = vals[-n:]
    mid = sum(window) / n
    var = sum((v - mid) ** 2 for v in window) / n
    sd = var ** 0.5
    return {"mid": round(mid, 4), "upper": round(mid + k * sd, 4),
            "lower": round(mid - k * sd, 4)}


def atr(candles, n=14):
    if len(candles) < n + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / n
    for i in range(n, len(trs)):
        a = (a * (n - 1) + trs[i]) / n
    return round(a, 4)


def _candles_upto(candles, when):
    when = _dt(when)
    return [c for c in candles if _dt(c["time"]) <= when]


def indicator_snapshot(candles, when):
    """All indicators as they stood at `when` (uses only candles up to then —
    no lookahead)."""
    upto = _candles_upto(candles, when)
    if len(upto) < 27:
        return None
    cl = _closes(upto)
    price = cl[-1]
    e20 = ema(cl, 20)
    snap = {
        "price": round(price, 4),
        "rsi14": rsi(cl, 14),
        "ema20": round(e20, 4) if e20 else None,
        "ema20_dist_pct": round((price - e20) / e20 * 100, 2) if e20 else None,
        "macd": macd(cl),
        "bollinger": bollinger(cl),
        "atr14": atr(upto, 14),
    }
    bb = snap["bollinger"]
    if bb:
        if price >= bb["upper"]:
            snap["bb_position"] = "at_or_above_upper"
        elif price <= bb["lower"]:
            snap["bb_position"] = "at_or_below_lower"
        else:
            snap["bb_position"] = "inside_bands"
    if snap["rsi14"] is not None:
        snap["rsi_state"] = ("overbought" if snap["rsi14"] >= 70
                             else "oversold" if snap["rsi14"] <= 30 else "neutral")
    return snap


def exit_efficiency(candles, side, exit_time, exit_price, lookahead_hours=12):
    """How much of the available move the exit captured. Looks at price AFTER
    the exit to see if the trade was closed early or late.

    Returns the most favourable price the market offered in the lookahead
    window and what fraction of that the exit captured. Purely descriptive of
    what already happened — never a suggestion about the future."""
    et = _dt(exit_time)
    fwd = [c for c in candles if et < _dt(c["time"]) <= et + timedelta(hours=lookahead_hours)]
    if not fwd:
        return None
    side = side.lower()
    if side in ("buy", "long"):
        best = max(c["high"] for c in fwd)          # more upside was available
        left_pct = round((best - exit_price) / exit_price * 100, 2)
        direction = "higher"
    else:
        best = min(c["low"] for c in fwd)            # more downside was available
        left_pct = round((exit_price - best) / exit_price * 100, 2)
        direction = "lower"
    return {
        "exit_price": round(exit_price, 4),
        "best_after_exit": round(best, 4),
        "extra_move_pct": max(left_pct, 0.0),
        "direction_after": direction,
        "lookahead_hours": lookahead_hours,
        "note": ("exit_was_early" if left_pct > 0.15 else "exit_near_optimal"),
    }


def entry_quality(candles, side, entry_time, entry_price, lookahead_hours=12):
    """Did the market immediately go the trade's way, or against it first?
    Descriptive only."""
    et = _dt(entry_time)
    fwd = [c for c in candles if et <= _dt(c["time"]) <= et + timedelta(hours=lookahead_hours)]
    if not fwd:
        return None
    side = side.lower()
    worst_adverse = 0.0
    for c in fwd:
        if side in ("buy", "long"):
            adverse = (entry_price - c["low"]) / entry_price * 100
        else:
            adverse = (c["high"] - entry_price) / entry_price * 100
        worst_adverse = max(worst_adverse, adverse)
    return {"max_adverse_pct": round(worst_adverse, 2),
            "note": ("entry_saw_drawdown" if worst_adverse > 0.5 else "entry_clean")}


# ---------------------------------------------------------------------------
# Extended chart analysis: volume, support/resistance, market structure,
# candlestick pattern, momentum. All computed from candles. No lookahead:
# every function uses only candles at or before `when`.

def _swings(candles, k=2):
    """Fractal swing highs/lows: a high greater than k neighbours each side
    (and mirror for lows). Returns (swing_highs, swing_lows) as lists of
    (index, price)."""
    highs, lows = [], []
    for i in range(k, len(candles) - k):
        h = candles[i]["high"]; l = candles[i]["low"]
        if all(h > candles[i - j]["high"] and h > candles[i + j]["high"] for j in range(1, k + 1)):
            highs.append((i, h))
        if all(l < candles[i - j]["low"] and l < candles[i + j]["low"] for j in range(1, k + 1)):
            lows.append((i, l))
    return highs, lows


def support_resistance(candles, when, lookback=60):
    """Nearest swing resistance above and support below the price at `when`,
    from the last `lookback` candles before it. Distances in %."""
    upto = _candles_upto(candles, when)[-lookback:]
    if len(upto) < 10:
        return None
    price = upto[-1]["close"]
    highs, lows = _swings(upto)
    res = [p for _, p in highs if p > price]
    sup = [p for _, p in lows if p < price]
    out = {"price": round(price, 4)}
    if res:
        r = min(res)
        out["resistance"] = round(r, 4)
        out["resistance_dist_pct"] = round((r - price) / price * 100, 2)
    if sup:
        s = max(sup)
        out["support"] = round(s, 4)
        out["support_dist_pct"] = round((price - s) / price * 100, 2)
    return out if ("resistance" in out or "support" in out) else None


def market_structure(candles, when, lookback=80):
    """Classify structure from the last swings before `when`:
    higher highs + higher lows -> UPTREND; lower highs + lower lows ->
    DOWNTREND; otherwise RANGE. Needs at least 2 swing highs and 2 lows."""
    upto = _candles_upto(candles, when)[-lookback:]
    highs, lows = _swings(upto)
    if len(highs) < 2 or len(lows) < 2:
        return None
    hh = highs[-1][1] > highs[-2][1]
    hl = lows[-1][1] > lows[-2][1]
    lh = highs[-1][1] < highs[-2][1]
    ll = lows[-1][1] < lows[-2][1]
    if hh and hl:
        return "UPTREND"
    if lh and ll:
        return "DOWNTREND"
    return "RANGE"


def candle_pattern(candles, when):
    """Simple single/two-candle pattern at the candle containing `when`:
    bullish/bearish engulfing, doji, hammer, shooting star. Returns a name or
    None. Deterministic OHLC geometry only."""
    upto = _candles_upto(candles, when)
    if len(upto) < 2:
        return None
    c, p = upto[-1], upto[-2]
    body = abs(c["close"] - c["open"]); rng = c["high"] - c["low"]
    if rng <= 0:
        return None
    upper = c["high"] - max(c["open"], c["close"])
    lower = min(c["open"], c["close"]) - c["low"]
    p_body_hi, p_body_lo = max(p["open"], p["close"]), min(p["open"], p["close"])
    if body / rng < 0.1:
        return "doji"
    if lower > 2 * body and upper < body:
        return "hammer"
    if upper > 2 * body and lower < body:
        return "shooting_star"
    if c["close"] > c["open"] and p["close"] < p["open"] \
            and c["close"] >= p_body_hi and c["open"] <= p_body_lo:
        return "bullish_engulfing"
    if c["close"] < c["open"] and p["close"] > p["open"] \
            and c["open"] >= p_body_hi and c["close"] <= p_body_lo:
        return "bearish_engulfing"
    return None


def momentum_roc(candles, when, n=10):
    """Rate of change over the last n candles before `when`, in %."""
    upto = _candles_upto(candles, when)
    if len(upto) < n + 1:
        return None
    now, past = upto[-1]["close"], upto[-n - 1]["close"]
    if not past:
        return None
    return round((now - past) / past * 100, 2)


def volume_read(candles, when, n=20):
    """Entry-candle volume vs the average of the prior n candles. Returns a
    ratio (1.0 = average) or None when the feed has no volume."""
    upto = _candles_upto(candles, when)
    if len(upto) < n + 1 or "volume" not in upto[-1]:
        return None
    vols = [c.get("volume") for c in upto[-n - 1:-1] if c.get("volume")]
    if not vols:
        return None
    avg = sum(vols) / len(vols)
    if avg <= 0:
        return None
    return round(upto[-1]["volume"] / avg, 2)


# ---------------------------------------------------------------------------
# Professional-grade additions: MFE, VWAP, OBV, ADX, RSI divergence.
# All deterministic, all no-lookahead (only candles at/before `when`).

def favorable_excursion(candles, side, entry_time, entry_price, exit_time):
    """Max favorable excursion during the hold: the best the trade EVER looked
    before exit, in %. Mirror of max adverse excursion."""
    et, xt = _dt(entry_time), _dt(exit_time)
    window = [c for c in candles if et <= _dt(c["time"]) <= xt]
    if not window:
        return None
    side = side.lower()
    best = 0.0
    for c in window:
        if side in ("buy", "long"):
            fav = (c["high"] - entry_price) / entry_price * 100
        else:
            fav = (entry_price - c["low"]) / entry_price * 100
        best = max(best, fav)
    return round(best, 2)


def vwap_read(candles, when, n=24):
    """Rolling VWAP of the last n candles before `when` (typical price x
    volume). Returns price distance from VWAP in %, or None without volume."""
    upto = _candles_upto(candles, when)[-n:]
    if len(upto) < 5 or "volume" not in upto[-1]:
        return None
    num = den = 0.0
    for c in upto:
        v = c.get("volume") or 0
        tp = (c["high"] + c["low"] + c["close"]) / 3
        num += tp * v
        den += v
    if den <= 0:
        return None
    vwap = num / den
    price = upto[-1]["close"]
    return {"vwap": round(vwap, 4),
            "dist_pct": round((price - vwap) / vwap * 100, 2)}


def obv_trend(candles, when, n=30, step=10):
    """On-balance volume direction: OBV now vs OBV `step` candles ago over the
    last n candles. Returns 'rising', 'falling' or 'flat'."""
    upto = _candles_upto(candles, when)[-n:]
    if len(upto) < step + 2 or "volume" not in upto[-1]:
        return None
    obv = [0.0]
    for i in range(1, len(upto)):
        v = upto[i].get("volume") or 0
        if upto[i]["close"] > upto[i - 1]["close"]:
            obv.append(obv[-1] + v)
        elif upto[i]["close"] < upto[i - 1]["close"]:
            obv.append(obv[-1] - v)
        else:
            obv.append(obv[-1])
    delta = obv[-1] - obv[-1 - step]
    scale = max(abs(x) for x in obv) or 1
    if abs(delta) < 0.05 * scale:
        return "flat"
    return "rising" if delta > 0 else "falling"


def adx(candles, when, n=14):
    """Wilder's ADX(14) with +DI/-DI at `when`. Returns dict or None."""
    upto = _candles_upto(candles, when)
    if len(upto) < 2 * n + 1:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(upto)):
        up = upto[i]["high"] - upto[i - 1]["high"]
        dn = upto[i - 1]["low"] - upto[i]["low"]
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        h, l, pc = upto[i]["high"], upto[i]["low"], upto[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    def wilder(series):
        s = sum(series[:n])
        out = [s]
        for x in series[n:]:
            s = s - s / n + x
            out.append(s)
        return out
    tr_s, pdm_s, mdm_s = wilder(trs), wilder(plus_dm), wilder(minus_dm)
    dxs = []
    for t, p, m in zip(tr_s, pdm_s, mdm_s):
        if t <= 0:
            continue
        pdi, mdi = 100 * p / t, 100 * m / t
        if pdi + mdi == 0:
            continue
        dxs.append(100 * abs(pdi - mdi) / (pdi + mdi))
    if len(dxs) < n:
        return None
    a = sum(dxs[:n]) / n
    for x in dxs[n:]:
        a = (a * (n - 1) + x) / n
    t, p, m = tr_s[-1], pdm_s[-1], mdm_s[-1]
    pdi = round(100 * p / t, 1) if t else None
    mdi = round(100 * m / t, 1) if t else None
    return {"adx": round(a, 1), "di_plus": pdi, "di_minus": mdi}


def rsi_divergence(candles, when, lookback=80):
    """Conservative two-swing divergence check before `when`: price higher
    high with lower RSI -> bearish divergence; price lower low with higher
    RSI -> bullish. Returns 'bearish', 'bullish' or None. Only fires on a
    clear signal (RSI gap >= 2 points)."""
    upto = _candles_upto(candles, when)[-lookback:]
    if len(upto) < 40:
        return None
    closes = [c["close"] for c in upto]
    highs, lows = _swings(upto)
    def rsi_at(i):
        return rsi(closes[: i + 1], 14)
    if len(highs) >= 2:
        (i1, p1), (i2, p2) = highs[-2], highs[-1]
        r1, r2 = rsi_at(i1), rsi_at(i2)
        if r1 is not None and r2 is not None and p2 > p1 and (r1 - r2) >= 2:
            return "bearish"
    if len(lows) >= 2:
        (i1, p1), (i2, p2) = lows[-2], lows[-1]
        r1, r2 = rsi_at(i1), rsi_at(i2)
        if r1 is not None and r2 is not None and p2 < p1 and (r2 - r1) >= 2:
            return "bullish"
    return None
