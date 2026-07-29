"""classifier.py — deterministic tone/risk routing. Thresholds from config."""


def classify(metrics: dict, config) -> dict:
    if metrics.get("num_trades", 0) == 0:
        return {"track": "SKIP", "reasons": ["No trades in window"]}

    reasons = []
    total_pl = metrics["total_pl"]
    max_dd = metrics["max_drawdown"]
    worst = metrics["worst_single_loss"]
    sl_set = metrics["sl_set_pct"]
    rr = metrics["reward_risk_ratio"]
    tpd = metrics["trades_per_day"]
    avg_hold = metrics["avg_hold_minutes"]

    # HUMAN_REVIEW — severe distress, never auto-sent
    if total_pl <= config.HUMAN_REVIEW_NET_LOSS:
        return {"track": "HUMAN_REVIEW", "reasons": [f"Large net loss ({total_pl})"]}
    if max_dd <= config.HUMAN_REVIEW_DRAWDOWN:
        return {"track": "HUMAN_REVIEW", "reasons": [f"Severe drawdown ({max_dd})"]}
    if worst <= config.HUMAN_REVIEW_SINGLE_LOSS:
        return {"track": "HUMAN_REVIEW", "reasons": [f"Very large single loss ({worst})"]}

    track = "STANDARD"
    if total_pl < 0:
        reasons.append(f"Net negative P/L ({total_pl})"); track = "SOFT"
    if sl_set < config.SOFT_SL_PCT:
        reasons.append(f"Stop-loss set on only {sl_set}% of trades"); track = "SOFT"
    if rr is not None and rr < config.SOFT_RR:
        reasons.append(f"Reward:risk below {config.SOFT_RR} ({rr})"); track = "SOFT"
    if max_dd <= config.SOFT_DRAWDOWN:
        reasons.append(f"Notable drawdown ({max_dd})"); track = "SOFT"
    if tpd >= config.OVERTRADE_PER_DAY:
        reasons.append(f"High frequency ({tpd}/day) - possible overtrading")
        if track == "STANDARD":
            track = "SOFT"
    if avg_hold < config.SCALP_HOLD_MIN:
        reasons.append(f"Very short avg hold ({avg_hold} min) - scalping/churn")

    if track == "STANDARD" and not reasons:
        reasons.append("Healthy metrics overall")
    return {"track": track, "reasons": reasons}
