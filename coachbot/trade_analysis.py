"""
trade_analysis.py — deterministic analytical findings from a trade log.
------------------------------------------------------------------------
NO LLM. NO external data. Turns raw trades into structured FINDINGS — the
judgments a report is built on (concentration, execution quality, directional
edge, instrument reliance, holding behaviour). The LLM later narrates these;
it does not compute them and cannot contradict them.

Input: list of trade dicts with at least symbol, side/direction, entry, close,
pnl, opened, closed. Leverage optional. Missing fields degrade gracefully.
"""
from __future__ import annotations
from datetime import datetime


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _move_pct(t):
    """Favorable move captured, signed by direction. None if prices missing."""
    entry, close = _f(t.get("entry")), t.get("close")
    if not entry or close in (None, "", "None"):
        return None
    close = _f(close)
    side = str(t.get("side", t.get("direction", ""))).upper()
    if "SHORT" in side or side == "SELL":
        return (entry - close) / entry * 100
    return (close - entry) / entry * 100


def analyse_trades(trades: list) -> dict:
    rows = []
    for t in trades:
        pnl = _f(t.get("pnl", t.get("pnl_usdt")))
        rows.append({
            "symbol": t.get("symbol", "?"),
            "side": "SHORT" if "SHORT" in str(t.get("side", t.get("direction", ""))).upper()
                    or str(t.get("side", t.get("direction", ""))).upper() == "SELL" else "LONG",
            "pnl": pnl,
            "lev": _f(t.get("lev", t.get("leverage")), 0),
            "move_pct": _move_pct(t),
            "opened": t.get("opened", t.get("open_time")),
            "closed": t.get("closed", t.get("close_time")),
        })
    n = len(rows)
    if n == 0:
        return {"n": 0}
    total = sum(r["pnl"] for r in rows)
    wins = [r for r in rows if r["pnl"] > 0]
    losses = [r for r in rows if r["pnl"] < 0]

    # concentration: how few trades carry the result
    srt = sorted(rows, key=lambda r: r["pnl"], reverse=True)
    top2 = srt[:2]
    top2_share = (sum(r["pnl"] for r in top2) / total * 100) if total else 0
    half, acc = 0, 0
    for r in srt:
        acc += r["pnl"]; half += 1
        if total and acc >= total * 0.5:
            break

    # execution: move captured vs leverage risked
    moves = [r["move_pct"] for r in rows if r["move_pct"] is not None]
    levs = [r["lev"] for r in rows if r["lev"] > 0]

    # directional + instrument edge
    def grp(key):
        out = {}
        for r in rows:
            k = r[key]
            g = out.setdefault(k, {"trades": 0, "net": 0.0, "moves": []})
            g["trades"] += 1; g["net"] += r["pnl"]
            if r["move_pct"] is not None:
                g["moves"].append(r["move_pct"])
        for k, g in out.items():
            g["net"] = round(g["net"])
            g["share_pct"] = round(g["net"] / total * 100, 1) if total else 0
            g["avg_move_pct"] = round(sum(g["moves"]) / len(g["moves"]), 2) if g["moves"] else None
            del g["moves"]
        return out

    # holding behaviour
    def _days(a, b):
        try:
            return (datetime.fromisoformat(str(b)[:10]) - datetime.fromisoformat(str(a)[:10])).days
        except Exception:
            return None
    holds = [d for r in rows if (d := _days(r["opened"], r["closed"])) is not None]

    return {
        "n": n,
        "total_pnl": round(total),
        "win_count": len(wins),
        "loss_count": len(losses),
        "all_winners": len(losses) == 0,
        "concentration": {
            "top2_share_pct": round(top2_share, 1),
            "top2_trades": [f"{r['symbol']} {r['side']} {r['pnl']:+.0f}" for r in top2],
            "trades_for_half_profit": half,
            "verdict": "highly concentrated" if top2_share > 35 else
                       "moderately concentrated" if top2_share > 20 else "well distributed",
        },
        "execution": {
            "avg_favorable_move_pct": round(sum(moves) / len(moves), 2) if moves else None,
            "move_range": [round(min(moves), 2), round(max(moves), 2)] if moves else None,
            "avg_leverage": round(sum(levs) / len(levs), 1) if levs else None,
        },
        "by_direction": grp("side"),
        "by_instrument": grp("symbol"),
        "holding": {
            "same_day_closes": sum(1 for d in holds if d == 0),
            "multi_day_holds": sum(1 for d in holds if d > 0),
            "max_hold_days": max(holds) if holds else None,
        },
    }
