"""analysis.py — deterministic per-user metrics. No LLM."""
from datetime import datetime
from statistics import mean


def _parse(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def _std(xs):
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def compute_metrics(trades: list) -> dict:
    n = len(trades)
    if n == 0:
        return {"num_trades": 0}

    profits = [float(t["profit"]) for t in trades]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]

    avg_win = round(mean(wins), 2) if wins else 0.0
    avg_loss = round(mean(losses), 2) if losses else 0.0
    rr = round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else None

    sl_set = sum(1 for t in trades if float(t["sl"]) > 0)
    holds = [(_parse(t["close_time"]) - _parse(t["open_time"])).total_seconds() / 60
             for t in trades]
    open_times = sorted(_parse(t["open_time"]) for t in trades)
    span_days = max((open_times[-1] - open_times[0]).days, 1)

    equity = peak = max_dd = 0.0
    for p in profits:
        equity += p
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    vols = [float(t["volume"]) for t in trades]

    return {
        "num_trades": n,
        "win_rate_pct": round(100 * len(wins) / n, 1),
        "total_pl": round(sum(profits), 2),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "reward_risk_ratio": rr,
        "sl_set_pct": round(100 * sl_set / n, 1),
        "sl_hit_count": sum(1 for t in trades if str(t["sl_hit"]) == "1"),
        "tp_hit_count": sum(1 for t in trades if str(t["tp_hit"]) == "1"),
        "avg_hold_minutes": round(mean(holds), 1),
        "trades_per_day": round(n / span_days, 2),
        "worst_single_loss": round(min(profits), 2),
        "max_drawdown": round(max_dd, 2),
        "position_size_variability": round((_std(vols) / mean(vols)), 2) if mean(vols) else 0.0,
    }
