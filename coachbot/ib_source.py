"""
ib_source.py — loads the IB directory and the client->IB mapping.
------------------------------------------------------------------
Reuses file_source's reader + alias machinery so IB data ingests exactly like
trades/clients do: drop a CSV/XLSX in data/, point env vars at it.

TWO INPUTS
----------
IBS_FILE    : one row per IB / partner
  canonical: ib_id, name, parent_ib_id, tier, rate_per_lot, telegram_id, email

CLIENT->IB mapping comes from the EXISTING clients file (USERS_FILE): if it has
an `ib_id` column we use it. So in most setups you only add ONE new file (the IB
directory) plus one new column on your client export.
"""
from __future__ import annotations
import os

from .file_source import _read_rows, _build_index, _to_float
from .logger import get_logger

log = get_logger("ib_src")

IB_ALIASES = {
    "ib_id":        ["ibid", "id", "partnerid", "ib", "ibcode", "agentid", "introducerid"],
    "name":         ["name", "ibname", "partnername", "fullname", "agent"],
    "parent_ib_id": ["parentibid", "parentid", "parent", "upline", "masterib", "uplineid"],
    "tier":         ["tier", "rank", "level", "ibtier", "partnertier"],
    "rate_per_lot": ["rateperlot", "rate", "commissionperlot", "perlot", "rebate", "rebateperlot"],
    "telegram_id":  ["telegramid", "telegram", "chatid", "tg"],
    "email":        ["email", "emailaddress", "mail"],
}

CLIENT_IB_ALIASES = {
    "login": ["login", "account", "accountid", "clientlogin", "userlogin", "mt5login"],
    "ib_id": ["ibid", "ib", "partnerid", "introducerid", "agentid", "referredby", "ibcode"],
}


def load_ibs(path: str) -> list:
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"IBS_FILE not found: {path}")
    header, rows = _read_rows(path)
    idx = _build_index(header, IB_ALIASES)
    missing = [f for f in ("ib_id", "name") if f not in idx]
    if missing:
        raise ValueError(f"IBS_FILE missing required columns {missing}. Found: {header}")
    ibs = []
    for row in rows:
        if not row or all(c == "" for c in row):
            continue
        def get(field, default=""):
            return row[idx[field]] if field in idx and idx[field] < len(row) else default
        ibs.append({
            "ib_id": str(get("ib_id")).strip(),
            "name": str(get("name")).strip(),
            "parent_ib_id": str(get("parent_ib_id")).strip() or None,
            "tier": str(get("tier")).strip(),
            "rate_per_lot": _to_float(get("rate_per_lot"), 0.0),
            "telegram_id": str(get("telegram_id")).strip(),
            "email": str(get("email")).strip(),
        })
    log.info("Loaded %d IBs from %s", len(ibs), path)
    return ibs


def load_client_ib_map(users_path: str) -> list:
    """
    Returns [{login, ib_id}, ...] from the clients file's ib_id column.
    Clients with no ib_id are simply omitted (direct clients of the house).
    """
    header, rows = _read_rows(users_path)
    idx = _build_index(header, CLIENT_IB_ALIASES)
    if "login" not in idx or "ib_id" not in idx:
        log.warning("Clients file has no ib_id column — no IB attribution available.")
        return []
    out = []
    for row in rows:
        if not row or all(c == "" for c in row):
            continue
        def get(field, default=""):
            return row[idx[field]] if field in idx and idx[field] < len(row) else default
        ib_id = str(get("ib_id")).strip()
        if not ib_id:
            continue
        out.append({"login": int(_to_float(get("login"))), "ib_id": ib_id})
    log.info("Loaded %d client->IB mappings", len(out))
    return out
