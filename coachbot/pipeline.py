"""
pipeline.py — the full run, wiring every phase in order.

  consent gate  -> we only ever fetch users from MT5 groups the broker has
                   designated as opted-in (config.MT5_GROUPS). A user with no
                   telegram handle is also un-reachable -> queued, not sent.
  analysis      -> deterministic metrics
  classifier    -> STANDARD / SOFT / HUMAN_REVIEW / SKIP
  generation    -> constrained Claude (or mock)
  compliance    -> output filter
  delivery      -> Telegram (dry-run unless SEND_FOR_REAL=1)
  audit + state -> log everything, never double-send in a day
"""
from datetime import datetime, timedelta, timezone

from .config import config
from .logger import get_logger
from .mt5_source import get_data_source
from .analysis import compute_metrics
from .classifier import classify
from .generation import generate_message
from .compliance import check_output
from .telegram import send_message
from . import store

log = get_logger("pipeline")


def _first_name(full):
    return (full or "Trader").split()[0]


def run_once(force=False):
    problems = config.validate_for_send()
    for p in problems:
        log.warning("CONFIG: %s", p)

    day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Naive UTC: trade timestamps from all sources are naive UTC strings, so we
    # keep `since` naive too for consistent comparisons.
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=config.LOOKBACK_DAYS)
    state = store.load_state(config.STATE_PATH)

    summary = {"sent": 0, "human_review": 0, "filter_blocked": 0,
               "unreachable": 0, "skipped_no_trades": 0, "already_sent": 0, "errors": 0}

    src = get_data_source(config)
    src.connect()
    try:
        users = src.get_users()
        log.info("Processing %d users | window=%dd | mode=%s",
                 len(users), config.LOOKBACK_DAYS,
                 "DRY-RUN" if config.DRY_RUN else "LIVE")

        for user in users:
            login = user["login"]
            name = user["name"]

            if not force and store.already_sent_today(state, login, day_key):
                summary["already_sent"] += 1
                continue

            try:
                trades = src.get_closed_deals(login, since)
                metrics = compute_metrics(trades)
                result = classify(metrics, config)
                track, reasons = result["track"], result["reasons"]

                if track == "SKIP":
                    summary["skipped_no_trades"] += 1
                    continue

                if track == "HUMAN_REVIEW":
                    store.append_jsonl(config.REVIEW_QUEUE_PATH, {
                        "login": login, "name": name, "email": user.get("email"),
                        "telegram_id": user.get("telegram_id"), "track": track,
                        "routed_reason": "severe_risk_signals", "reasons": reasons,
                        "metrics": metrics,
                    })
                    summary["human_review"] += 1
                    log.info("[%s] %s -> HUMAN_REVIEW", login, name)
                    continue

                message = generate_message(_first_name(name), track, metrics, config)

                fr = check_output(message)
                if not fr["passed"]:
                    store.append_jsonl(config.REVIEW_QUEUE_PATH, {
                        "login": login, "name": name, "track": track,
                        "routed_reason": "failed_compliance_filter",
                        "violations": fr["violations"], "message": message,
                        "metrics": metrics,
                    })
                    summary["filter_blocked"] += 1
                    log.info("[%s] %s -> BLOCKED by filter", login, name)
                    continue

                chat = user.get("telegram_id")
                if not chat:
                    store.append_jsonl(config.REVIEW_QUEUE_PATH, {
                        "login": login, "name": name, "track": track,
                        "routed_reason": "no_telegram_handle", "message": message,
                        "metrics": metrics,
                    })
                    summary["unreachable"] += 1
                    log.info("[%s] %s -> unreachable (no telegram)", login, name)
                    continue

                delivery = send_message(config.TELEGRAM_BOT_TOKEN, chat, message,
                                        dry_run=config.DRY_RUN)

                store.append_jsonl(config.AUDIT_PATH, {
                    "login": login, "name": name, "track": track,
                    "compliance_passed": True, "message": message,
                    "delivery": delivery, "dry_run": config.DRY_RUN,
                    "day_key": day_key,
                })

                if delivery.get("status") in ("sent", "DRY_RUN"):
                    store.mark_sent(state, login, day_key)
                    summary["sent"] += 1
                    log.info("[%s] %s -> %s -> %s", login, name, track, delivery["status"])
                else:
                    summary["errors"] += 1
                    log.error("[%s] %s -> delivery failed: %s", login, name, delivery)

            except Exception as e:
                summary["errors"] += 1
                log.exception("[%s] %s -> error: %s", login, name, e)

        store.save_state(config.STATE_PATH, state)
    finally:
        src.disconnect()

    log.info("SUMMARY %s", summary)
    return summary
