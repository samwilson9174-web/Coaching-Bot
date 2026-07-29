"""
market_context.py — deterministic per-trade market context. No LLM here.
--------------------------------------------------------------------------
For each closed trade, fetches the instrument's price path around the trade
window and computes plain numeric facts:

  market_move_pct   — how the market moved between entry and exit
  alignment         — WITH_MARKET / AGAINST_MARKET for the trade's direction
  range_pct         — high-low range during the hold (volatility context)
  post_exit_move_pct— what price did in the hours AFTER the exit

These numbers are handed to Claude as data. Claude is NEVER asked to recall
market events from memory — an LLM asked "what happened in gold that day"
will fabricate plausible-sounding news. Every market fact in the report must
originate here, from a data provider, or not appear at all.

Providers (MARKET_PROVIDER env):
  mock       — synthetic but internally consistent candles; for testing.
  twelvedata — Twelve Data HTTP API (needs MARKET_API_KEY). Written to spec
               but NOT live-tested from this environment; verify on first use.
Add others (Polygon, Alpha Vantage, your own MT5 history dump) by matching
the two-method interface.
"""
from __future__ import annotations
import hashlib
import json
import random
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .logger import get_logger
from .indicators import indicator_snapshot, exit_efficiency, entry_quality

log = get_logger("market")

FMT = "%Y-%m-%d %H:%M:%S"


def _dt(s: str) -> datetime:
    return datetime.strptime(s, FMT)


# ---------------------------------------------------------------------------
class MockMarketProvider:
    """Deterministic synthetic hourly candles (seeded by symbol+hour)."""

    def get_candles(self, symbol: str, start: datetime, end: datetime) -> list:
        seed = int(hashlib.sha1(symbol.encode()).hexdigest()[:8], 16)
        base = 1000 + (seed % 5000) / 3.0
        out, t = [], start.replace(minute=0, second=0)
        price = base
        while t <= end:
            rng = random.Random(seed ^ int(t.timestamp()))
            drift = rng.uniform(-0.004, 0.004)
            o = price
            c = o * (1 + drift)
            hi = max(o, c) * (1 + rng.uniform(0, 0.002))
            lo = min(o, c) * (1 - rng.uniform(0, 0.002))
            out.append({"time": t.strftime(FMT), "open": o, "high": hi,
                        "low": lo, "close": c,
                        "volume": 100 * (1 + rng.uniform(-0.5, 1.5))})
            price = c
            t += timedelta(hours=1)
        return out


class TwelveDataProvider:
    """Real hourly candles from Twelve Data. Requires MARKET_API_KEY.
    NOTE: written to their documented time_series API shape but not
    live-tested from this build environment — verify the first response."""

    BASE = "https://api.twelvedata.com/time_series"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("MARKET_API_KEY required for twelvedata provider")
        self.key = api_key

    @staticmethod
    def _map_symbol(symbol: str) -> str:
        s = symbol.upper()
        if len(s) == 6 and s.isalpha():          # EURUSD -> EUR/USD
            return f"{s[:3]}/{s[3:]}"
        if s.endswith("USD") and len(s) > 6:     # BTCUSD -> BTC/USD
            return f"{s[:-3]}/USD"
        return s                                  # indices/metals: try as-is

    def get_candles(self, symbol: str, start: datetime, end: datetime) -> list:
        params = urllib.parse.urlencode({
            "symbol": self._map_symbol(symbol), "interval": "1h",
            "start_date": start.strftime(FMT), "end_date": end.strftime(FMT),
            "apikey": self.key, "format": "JSON", "order": "ASC",
        })
        with urllib.request.urlopen(f"{self.BASE}?{params}", timeout=30) as r:
            data = json.loads(r.read().decode())
        if data.get("status") == "error" or "values" not in data:
            raise RuntimeError(f"twelvedata error for {symbol}: {data.get('message', data)}")
        return [{"time": v["datetime"] if len(v["datetime"]) > 10 else v["datetime"] + " 00:00:00",
                 "open": float(v["open"]), "high": float(v["high"]),
                 "low": float(v["low"]), "close": float(v["close"])}
                for v in data["values"]]



class BinanceProvider:
    """Real OHLC candles from Binance's PUBLIC klines endpoint.
    No API key, no account, no signup — it's an open public URL.
    Docs: GET /api/v3/klines?symbol=&interval=&startTime=&endTime=&limit=

    Binance returns an array of arrays; index map:
      [0]=openTime(ms) [1]=open [2]=high [3]=low [4]=close [5]=volume
      [6]=closeTime(ms) ...  We use 0-4.
    Interval is chosen from the requested span so short scalps get minute
    candles and multi-day swings get hourly — keeping payloads sane while
    staying granular enough for the indicator math."""

    BASE = "https://api.binance.com/api/v3/klines"

    def __init__(self, base_url: str = ""):
        # allow override, e.g. Binance Futures (fapi) or a regional mirror
        if base_url:
            self.BASE = base_url

    @staticmethod
    def _map_symbol(symbol: str) -> str:
        # ETHUSDT / BTCUSDT already match Binance spot symbols; strip separators
        return symbol.upper().replace("/", "").replace("-", "").replace("_", "")

    @staticmethod
    def _pick_interval(span_hours: float) -> str:
        if span_hours <= 6:
            return "1m"
        if span_hours <= 48:
            return "5m"
        if span_hours <= 24 * 20:
            return "1h"
        return "4h"

    def get_candles(self, symbol: str, start: datetime, end: datetime) -> list:
        sym = self._map_symbol(symbol)
        span_h = (end - start).total_seconds() / 3600
        interval = self._pick_interval(span_h)
        start_ms = int(start.replace(tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(end.replace(tzinfo=timezone.utc).timestamp() * 1000)

        out = []
        cursor = start_ms
        # Binance caps at 1000 candles/call; page through the window.
        for _ in range(50):  # hard stop so a bad range can't loop forever
            params = urllib.parse.urlencode({
                "symbol": sym, "interval": interval,
                "startTime": cursor, "endTime": end_ms, "limit": 1000})
            req = urllib.request.Request(f"{self.BASE}?{params}",
                                         headers={"User-Agent": "coachbot/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                rows = json.loads(r.read().decode())
            if not rows:
                break
            for k in rows:
                out.append({
                    "time": datetime.utcfromtimestamp(k[0] / 1000).strftime(FMT),
                    "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4])})
            last_open = rows[-1][0]
            if len(rows) < 1000 or last_open >= end_ms:
                break
            cursor = last_open + 1  # next page starts after last candle
        return out


def get_market_provider(config):
    p = (getattr(config, "MARKET_PROVIDER", "mock") or "mock").lower()
    if p == "mock":
        return MockMarketProvider()
    if p == "twelvedata":
        return TwelveDataProvider(getattr(config, "MARKET_API_KEY", ""))
    if p == "binance":
        return BinanceProvider(getattr(config, "BINANCE_KLINES_URL", ""))
    raise ValueError(f"Unknown MARKET_PROVIDER: {p!r} (use mock | twelvedata | binance)")


# ---------------------------------------------------------------------------
def _price_at(candles: list, when: datetime) -> float | None:
    """Close of the last candle at or before `when`."""
    best = None
    for c in candles:
        if _dt(c["time"]) <= when:
            best = c
        else:
            break
    return best["close"] if best else None


def build_trade_context(trade: dict, provider, post_hours: int = 4) -> dict | None:
    """Numeric market context for one closed trade. Returns None if data
    is unavailable (report simply covers that trade without market context)."""
    try:
        o, c = _dt(trade["open_time"]), _dt(trade["close_time"])
    except Exception:
        return None
    try:
        candles = provider.get_candles(trade["symbol"], o - timedelta(hours=48),
                                       c + timedelta(hours=post_hours))
    except Exception as e:
        log.warning("No market data for %s (%s): %s", trade.get("symbol"), o, e)
        return None
    if len(candles) < 2:
        return None

    p_open = _price_at(candles, o)
    p_close = _price_at(candles, c)
    p_post = _price_at(candles, c + timedelta(hours=post_hours))
    if not p_open or not p_close:
        return None

    in_window = [x for x in candles if o <= _dt(x["time"]) <= c] or candles
    hi = max(x["high"] for x in in_window)
    lo = min(x["low"] for x in in_window)

    move_pct = round((p_close - p_open) / p_open * 100, 2)
    direction = str(trade.get("type", trade.get("direction", ""))).lower()
    if direction not in ("buy", "sell"):
        alignment = "UNKNOWN"
    elif (move_pct >= 0) == (direction == "buy"):
        alignment = "WITH_MARKET"
    else:
        alignment = "AGAINST_MARKET"

    ctx = {
        "symbol": trade["symbol"],
        "direction": direction,
        "lots": trade.get("volume"),
        "profit": trade.get("profit"),
        "open_time": trade["open_time"],
        "close_time": trade["close_time"],
        "sl_was_set": bool(trade.get("sl")),
        "market_move_pct": move_pct,
        "alignment": alignment,
        "range_pct": round((hi - lo) / p_open * 100, 2),
    }
    if p_post:
        ctx["post_exit_move_pct"] = round((p_post - p_close) / p_close * 100, 2)

    # --- technical indicators + timing analysis (all from real candles) ---
    entry_snap = indicator_snapshot(candles, trade["open_time"])
    if entry_snap:
        ctx["indicators_at_entry"] = entry_snap
    ee = exit_efficiency(candles, direction, trade["close_time"], p_close)
    if ee:
        ctx["exit_efficiency"] = ee
    eq = entry_quality(candles, direction, trade["open_time"], p_open)
    if eq:
        ctx["entry_quality"] = eq

    # --- deterministic plain-English verdicts -------------------------------
    # These are FINISHED sentences the model must use as-is. They exist because
    # letting the model interpret raw signed fields (market_move_pct + alignment
    # + adverse move) produced inconsistent and sometimes contradictory prose
    # ("moved against you ... aligned"). Facts are decided here, once, in code.
    profit = trade.get("profit", 0) or 0
    won = profit > 0
    side_word = "long" if direction == "buy" else "short"
    facts = []

    # 1) result + whether the market direction helped or fought the trade
    if alignment == "WITH_MARKET":
        if won:
            facts.append(
                f"This {side_word} was on the same side as the market, which "
                f"moved {abs(move_pct):.2f}% in the trade's favour during the "
                f"hold, and the trade closed in profit.")
        else:
            facts.append(
                f"This {side_word} was on the same side as the market "
                f"({abs(move_pct):.2f}% in the trade's favour during the hold), "
                f"but still closed as a small loss, most likely to costs or the "
                f"exact entry and exit levels.")
    elif alignment == "AGAINST_MARKET":
        if won:
            facts.append(
                f"The market moved {abs(move_pct):.2f}% against this {side_word} "
                f"during the hold, yet the trade still closed in profit.")
        else:
            facts.append(
                f"The market moved {abs(move_pct):.2f}% against this {side_word} "
                f"during the hold, and the trade closed as a loss.")

    # 2) entry cleanliness (adverse excursion)
    if eq:
        adv = eq.get("max_adverse_pct", 0)
        if adv <= 0.3:
            facts.append(f"The entry was clean: price moved at most {adv:.2f}% "
                         f"against the position before it worked out.")
        else:
            facts.append(f"The entry was tested: price moved {adv:.2f}% against "
                         f"the position before recovering.")

    # 3) exit efficiency (early / near-optimal) — direction-correct
    if ee:
        extra = ee.get("extra_move_pct", 0)
        if ee.get("note") == "exit_was_early" and extra > 0.15:
            facts.append(f"The exit was early: after closing, price continued a "
                         f"further {extra:.2f}% in the trade's favour, which this "
                         f"exit did not capture.")
        else:
            facts.append("The exit was well-timed: little additional move was "
                         "available after closing.")

    # 4) indicator context at entry (stated, not interpreted)
    if entry_snap and entry_snap.get("rsi14") is not None:
        rsi_v = entry_snap["rsi14"]
        state = entry_snap.get("rsi_state", "neutral")
        facts.append(f"At entry RSI(14) was {rsi_v} ({state}).")

    ctx["verdicts"] = facts
    ctx["result"] = "win" if won else "loss"
    return ctx
