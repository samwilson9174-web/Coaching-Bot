"""
ib_pipeline.py — IB commission run. Deterministic, no LLM, no messaging side
effects by default (it's a calculation + report, not a sender).

Flow:
  load IB directory + client->IB map  ->  pull each client's trades (same data
  source as the coach)  ->  compute_ib_commissions  ->  write report + return.

Reuses the configured DATA_SOURCE so commission is computed on the exact same
normalised trades the coaching bot sees — single source of truth.
"""
from datetime import datetime, timedelta, timezone

from .config import config
from .logger import get_logger
from .mt5_source import get_data_source
from .ib_source import load_ibs, load_client_ib_map
from .ib_commission import compute_ib_commissions, compute_bbook_share, summarise
from . import store

log = get_logger("ib_pipe")


def run_ib_commissions(write_report=True):
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=config.LOOKBACK_DAYS)

    ibs = load_ibs(config.IBS_FILE)
    clients = load_client_ib_map(config.USERS_FILE)
    if not clients:
        log.warning("No client->IB mappings found; nothing to attribute.")

    src = get_data_source(config)
    src.connect()
    trades_by_login = {}
    try:
        for c in clients:
            login = c["login"]
            if login not in trades_by_login:
                trades_by_login[login] = src.get_closed_deals(login, since)
    finally:
        src.disconnect()

    results = compute_ib_commissions(clients, ibs, trades_by_login, config)

    if config.IB_BBOOK_SHARE_ENABLED and config.IB_BBOOK_SHARE_PCT > 0:
        bbook = compute_bbook_share(clients, ibs, trades_by_login, config.IB_BBOOK_SHARE_PCT)
        for ib_id, amt in bbook.items():
            if ib_id in results:
                results[ib_id]["bbook_share"] = amt
                results[ib_id]["total_commission"] = round(
                    results[ib_id]["total_commission"] + amt, 2)

    roll = summarise(results)
    log.info("IB SUMMARY %s", roll)

    if write_report:
        day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for r in results.values():
            store.append_jsonl(config.IB_REPORT_PATH, {**r, "day_key": day_key,
                                                       "lookback_days": config.LOOKBACK_DAYS})

    return {"summary": roll, "results": results}
