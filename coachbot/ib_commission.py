"""
ib_commission.py — deterministic IB commission engine. No LLM.
-----------------------------------------------------------------
Computes Introducing Broker (IB) payouts from the SAME normalised trade
records the coaching pipeline already uses (login, symbol, volume, profit).

MODEL: cascading commission-differential ladder (matches the brokerage's
ib-commission-logic spec). The broker sets a per-lot pool. Each IB tier earns
a slice; a senior IB earns the *difference* between its own rate and the rate
of the sub-IB beneath it, on every lot its downline generates.

INPUTS
------
clients : [{login, ib_id}, ...]            # which IB each trading account belongs to
ibs     : [{ib_id, name, parent_ib_id,     # IB directory + tier
            tier, rate_per_lot, telegram_id, email}, ...]
trades  : normalised trade records (login, symbol, volume, profit, ...)

RATE RESOLUTION (per lot), highest priority first:
  1. per-(ib, symbol_group) override   (rates_by_symbol_group, optional)
  2. ib's own rate_per_lot             (from the IB directory)
  3. tier default rate                 (config.IB_TIER_RATES)
  4. config.IB_DEFAULT_RATE_PER_LOT    (final fallback)

VOLUME is taken from each closed trade's `volume` field (standard lots).
Commission is volume-based and independent of client P/L (rebate model), which
is the compliance-safe default. A B-Book revenue-share variant is provided
separately (compute_bbook_share) for brokers who pay IBs on net client loss —
keep that OFF unless compliance signs off, since it changes incentives.

OUTPUT (per IB):
  {
    ib_id, name, tier,
    direct_lots, direct_commission,        # from own referred clients
    downline_lots, downline_commission,    # differential earned on sub-IBs
    total_commission,
    client_count, active_client_count,
    breakdown: [...per-client rows...]
  }
"""
from __future__ import annotations
from collections import defaultdict

from .logger import get_logger

log = get_logger("ib")


# --- symbol grouping (for per-asset-class rate overrides) ------------------
def symbol_group(symbol: str) -> str:
    s = (symbol or "").upper()
    if any(m in s for m in ("BTC", "ETH", "USDT", "SOL", "XRP", "DOGE")):
        return "CRYPTO"
    if any(m in s for m in ("XAU", "XAG", "GOLD", "SILVER", "WTI", "BRENT", "OIL", "NGAS")):
        return "METALS_ENERGY"
    if any(m in s for m in ("US30", "NAS", "SPX", "GER", "UK100", "JP225", "US500")):
        return "INDICES"
    # crude FX heuristic: 6-letter pair of currency codes
    if len(s) == 6 and s.isalpha():
        return "FX"
    return "OTHER"


def _resolve_rate(ib: dict, sym_group: str, config) -> float:
    """Per-lot rate for this IB on this symbol group, by priority."""
    overrides = getattr(config, "IB_RATES_BY_SYMBOL_GROUP", {}) or {}
    key = (str(ib["ib_id"]), sym_group)
    if key in overrides:
        return float(overrides[key])
    if ib.get("rate_per_lot") not in (None, "", 0, 0.0):
        return float(ib["rate_per_lot"])
    tier_rates = getattr(config, "IB_TIER_RATES", {}) or {}
    tier = str(ib.get("tier", "")).strip()
    if tier in tier_rates:
        return float(tier_rates[tier])
    return float(getattr(config, "IB_DEFAULT_RATE_PER_LOT", 5.0))


def _index_ibs(ibs: list) -> dict:
    return {str(ib["ib_id"]): ib for ib in ibs}


def _ancestors(ib_id: str, by_id: dict, max_depth: int = 10):
    """Yield (ancestor_ib, depth) walking up the parent chain. depth 1 = direct parent."""
    seen = set()
    cur = by_id.get(str(ib_id))
    depth = 0
    while cur is not None:
        parent_id = cur.get("parent_ib_id")
        if parent_id in (None, "", "0", 0):
            return
        parent_id = str(parent_id)
        if parent_id in seen or parent_id not in by_id:  # cycle / dangling guard
            return
        seen.add(parent_id)
        depth += 1
        if depth > max_depth:
            return
        yield by_id[parent_id], depth
        cur = by_id[parent_id]


def compute_ib_commissions(clients: list, ibs: list, trades_by_login: dict, config) -> dict:
    """
    Returns {ib_id: result_dict}. trades_by_login maps login -> [trade, ...].
    """
    by_id = _index_ibs(ibs)
    client_ib = {int(c["login"]): str(c["ib_id"]) for c in clients
                 if c.get("ib_id") not in (None, "", "0", 0)}

    # accumulators
    direct_lots = defaultdict(float)
    direct_comm = defaultdict(float)
    downline_lots = defaultdict(float)
    downline_comm = defaultdict(float)
    client_lots = defaultdict(float)        # (ib_id, login) -> lots
    client_comm = defaultdict(float)        # (ib_id, login) -> commission credited to *that* ib
    active_clients = defaultdict(set)
    all_clients = defaultdict(set)

    for login, ib_id in client_ib.items():
        if ib_id not in by_id:
            log.warning("client %s references unknown ib_id %s — skipped", login, ib_id)
            continue
        all_clients[ib_id].add(login)

        for t in trades_by_login.get(login, []):
            lots = float(t.get("volume", 0) or 0)
            if lots <= 0:
                continue
            sg = symbol_group(t.get("symbol", ""))
            active_clients[ib_id].add(login)

            # --- direct IB: earns the difference between its own rate and the
            #     rate of the FIRST sub-IB below it on this client's chain.
            #     For a directly-referring IB (no sub-IB between it and client),
            #     "downstream rate" is 0, so it earns its full rate. ---
            direct_ib = by_id[ib_id]
            direct_rate = _resolve_rate(direct_ib, sg, config)

            direct_lots[ib_id] += lots
            credit = direct_rate * lots
            direct_comm[ib_id] += credit
            client_lots[(ib_id, login)] += lots
            client_comm[(ib_id, login)] += credit

            # --- ancestors: each earns (its rate − child's rate) × lots, i.e.
            #     the differential. Never negative. ---
            child_rate = direct_rate
            for ancestor, _depth in _ancestors(ib_id, by_id):
                a_id = str(ancestor["ib_id"])
                a_rate = _resolve_rate(ancestor, sg, config)
                diff = max(a_rate - child_rate, 0.0)
                if diff > 0:
                    downline_lots[a_id] += lots
                    downline_comm[a_id] += diff * lots
                    active_clients[a_id].add(login)
                child_rate = a_rate

    results = {}
    for ib_id, ib in by_id.items():
        dc = round(direct_comm[ib_id], 2)
        wc = round(downline_comm[ib_id], 2)
        breakdown = [
            {"login": login, "lots": round(client_lots[(ib_id, login)], 2),
             "commission": round(client_comm[(ib_id, login)], 2)}
            for (i, login) in client_lots if i == ib_id
        ]
        breakdown.sort(key=lambda r: r["commission"], reverse=True)
        results[ib_id] = {
            "ib_id": ib_id,
            "name": ib.get("name", ib_id),
            "tier": ib.get("tier", ""),
            "parent_ib_id": ib.get("parent_ib_id") or None,
            "telegram_id": ib.get("telegram_id", ""),
            "email": ib.get("email", ""),
            "direct_lots": round(direct_lots[ib_id], 2),
            "direct_commission": dc,
            "downline_lots": round(downline_lots[ib_id], 2),
            "downline_commission": wc,
            "total_commission": round(dc + wc, 2),
            "client_count": len(all_clients[ib_id]),
            "active_client_count": len(active_clients[ib_id]),
            "breakdown": breakdown,
        }
    return results


def compute_bbook_share(clients: list, ibs: list, trades_by_login: dict,
                        share_pct: float) -> dict:
    """
    OPTIONAL revenue-share variant: pay each *direct* IB a percentage of the
    NET LOSS its referred clients booked (i.e. broker B-Book P&L). Returns
    {ib_id: bbook_commission}. Disabled by default — turning IB pay into a
    function of client losses is a conflict-of-interest / conduct concern and
    must be reviewed by compliance before use.
    """
    by_id = _index_ibs(ibs)
    client_ib = {int(c["login"]): str(c["ib_id"]) for c in clients
                 if c.get("ib_id") not in (None, "", "0", 0)}
    out = defaultdict(float)
    for login, ib_id in client_ib.items():
        if ib_id not in by_id:
            continue
        client_pl = sum(float(t.get("profit", 0) or 0)
                        for t in trades_by_login.get(login, []))
        broker_pl = -client_pl                      # broker gains when client loses
        if broker_pl > 0:
            out[ib_id] += broker_pl * (share_pct / 100.0)
    return {k: round(v, 2) for k, v in out.items()}


def summarise(results: dict) -> dict:
    """Roll-up totals for a run header / dashboard tile."""
    return {
        "ib_count": len(results),
        "total_commission": round(sum(r["total_commission"] for r in results.values()), 2),
        "total_direct_lots": round(sum(r["direct_lots"] for r in results.values()), 2),
        "ibs_with_activity": sum(1 for r in results.values() if r["total_commission"] > 0),
    }
