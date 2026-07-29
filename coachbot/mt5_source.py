"""
mt5_source.py — DATA INGESTION (Phase 2 source)
-----------------------------------------------
Pulls closed deals + account/client info from the MT5 Manager API and normalises
them into the same record shape the rest of the pipeline expects.

ABOUT THE MT5 MANAGER API
-------------------------
MetaQuotes ships the Manager API as a native library (MT5Manager) plus an
official Python binding (`MT5Manager` wheel from your broker portal — it is NOT
on PyPI, you get it with your MT5 server licence). It only runs where that
binding + the gateway are reachable (typically a Windows host near your MT5
server). Because of that, this module isolates ALL vendor-specific calls behind
one class with a tiny interface:

    src = MT5DataSource(config)
    src.connect()
    users  = src.get_users()                      # [{login, name, email, telegram_id}, ...]
    deals  = src.get_closed_deals(login, since)    # normalised closed trades
    src.disconnect()

Two implementations:
  - RealMT5DataSource  : uses the MT5Manager binding (runs on your MT5 host)
  - MockMT5DataSource  : synthetic data so you can run the whole bot anywhere

config.USE_MOCK_DATA selects which one `get_data_source()` returns.

NORMALISED TRADE RECORD (what downstream code consumes):
    {
      login, ticket, symbol, type ('buy'/'sell'), volume,
      open_price, close_price, sl, tp, sl_hit, tp_hit,
      open_time 'YYYY-MM-DD HH:MM:SS', close_time, profit
    }
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import random

from .logger import get_logger

log = get_logger("mt5")


# ===========================================================================
# REAL implementation — runs on your MT5 host with the MT5Manager binding.
# ===========================================================================
class RealMT5DataSource:
    def __init__(self, config):
        self.config = config
        self._mgr = None

    def connect(self):
        # The MT5Manager binding is imported lazily so the rest of the bot can
        # run on machines that don't have it (e.g. for mock/testing).
        import MT5Manager  # provided by your MT5 server licence, not PyPI

        self._mgr = MT5Manager.ManagerAPI()
        host, _, port = self.config.MT5_SERVER.partition(":")
        ok = self._mgr.Connect(
            self.config.MT5_SERVER,
            self.config.MT5_MANAGER_LOGIN,
            self.config.MT5_MANAGER_PASSWORD,
            MT5Manager.ManagerAPI.EnPumpModes.PUMP_MODE_FULL,
            120000,  # timeout ms
        )
        if not ok:
            raise ConnectionError(f"MT5 Manager connect failed: {MT5Manager.LastError()}")
        log.info("Connected to MT5 Manager at %s", self.config.MT5_SERVER)

    def disconnect(self):
        if self._mgr:
            try:
                self._mgr.Disconnect()
            except Exception:
                pass

    def get_users(self):
        """Return client records for the configured groups."""
        users = []
        for group in self.config.MT5_GROUPS:
            logins = self._mgr.UserLogins(group)  # list of int logins in group
            for login in logins or []:
                u = self._mgr.UserGet(login)
                if not u:
                    continue
                # Telegram handle convention: stored in the user's Comment or a
                # custom field. Adjust to wherever your CRM writes it.
                telegram = _extract_telegram(getattr(u, "Comment", "") or "")
                users.append({
                    "login": int(u.Login),
                    "name": (u.Name or "").strip(),
                    "email": (getattr(u, "EMail", "") or "").strip(),
                    "telegram_id": telegram,
                })
        log.info("Fetched %d users across %d groups", len(users), len(self.config.MT5_GROUPS))
        return users

    def get_closed_deals(self, login, since: datetime):
        """Pull deals for one login since `since`, pair them into closed trades."""
        frm = int(since.timestamp())
        to = int(datetime.now(timezone.utc).timestamp())
        deals = self._mgr.DealRequest(login, frm, to)  # array of MTDeal
        return _pair_deals_into_trades(login, deals or [])


# ===========================================================================
# MOCK implementation — synthetic data, runs anywhere.
# ===========================================================================
class MockMT5DataSource:
    """Generates the same behavioural profiles used in the prototype."""
    _PROFILES = [
        (50001, "Aisha Khan",   "aisha@example.com",  "@aisha_k",  "disciplined"),
        (50002, "Bilal Ahmed",  "bilal@example.com",  "@bilal_fx", "no_stoploss"),
        (50003, "Chen Wei",     "chen@example.com",   "@chenwei",  "overtrader"),
        (50004, "Diego Martin", "diego@example.com",  "@diego_m",  "blown_account"),
        (50005, "Fatima Noor",  "fatima@example.com", "@fatima_n", "disciplined"),
    ]

    def __init__(self, config):
        self.config = config
        random.seed(42)

    def connect(self):
        log.info("Using MOCK MT5 data source (USE_MOCK_DATA=1)")

    def disconnect(self):
        pass

    def get_users(self):
        return [
            {"login": l, "name": n, "email": e, "telegram_id": t}
            for (l, n, e, t, _p) in self._PROFILES
        ]

    def get_closed_deals(self, login, since: datetime):
        profile = next((p for (l, *_r, p) in self._PROFILES if l == login), "disciplined")
        return _gen_mock_trades(login, profile, since)


# ---------------------------------------------------------------------------
# Helpers shared by both implementations
# ---------------------------------------------------------------------------
def _extract_telegram(comment: str):
    for token in comment.replace(",", " ").split():
        if token.startswith("@"):
            return token
    return ""


def _pair_deals_into_trades(login, deals):
    """
    MT5 returns DEALS (entries/exits). A round-trip trade = an IN deal + an OUT
    deal on the same position. We pair by PositionID and derive the normalised
    trade record. This mirrors how MT5 stores closed trades.
    """
    by_pos = {}
    for d in deals:
        pid = getattr(d, "PositionID", None)
        if pid is None:
            continue
        by_pos.setdefault(pid, []).append(d)

    trades = []
    for pid, ds in by_pos.items():
        ds.sort(key=lambda x: getattr(x, "Time", 0))
        entry = next((d for d in ds if getattr(d, "Entry", None) == 0), None)   # ENTRY_IN
        exit_ = next((d for d in ds if getattr(d, "Entry", None) == 1), None)   # ENTRY_OUT
        if not entry or not exit_:
            continue
        profit = sum(float(getattr(d, "Profit", 0)) for d in ds)
        # Action 0=buy 1=sell on the entry deal
        direction = "buy" if getattr(entry, "Action", 0) == 0 else "sell"
        sl = float(getattr(entry, "PriceSL", 0) or 0)
        tp = float(getattr(entry, "PriceTP", 0) or 0)
        close_price = float(getattr(exit_, "Price", 0) or 0)
        # SL/TP hit inference: exit reason fields vary by build; approximate by
        # comparing exit price to SL/TP. Your build may expose a Reason code —
        # prefer that if available.
        sl_hit = bool(sl) and abs(close_price - sl) <= abs(close_price) * 0.0005
        tp_hit = bool(tp) and abs(close_price - tp) <= abs(close_price) * 0.0005
        trades.append({
            "login": login,
            "ticket": pid,
            "symbol": getattr(entry, "Symbol", ""),
            "type": direction,
            "volume": float(getattr(entry, "Volume", 0)) / 10000.0,  # MT5 volume is in lots*10000
            "open_price": float(getattr(entry, "Price", 0) or 0),
            "close_price": close_price,
            "sl": sl,
            "tp": tp,
            "sl_hit": int(sl_hit),
            "tp_hit": int(tp_hit),
            "open_time": _fmt_ts(getattr(entry, "Time", 0)),
            "close_time": _fmt_ts(getattr(exit_, "Time", 0)),
            "profit": round(profit, 2),
        })
    return trades


def _fmt_ts(epoch):
    try:
        return datetime.utcfromtimestamp(int(epoch)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _gen_mock_trades(login, profile, since: datetime):
    symbols = ["EURUSD", "GBPUSD", "XAUUSD", "USDJPY", "BTCUSD"]
    counts = {"disciplined": 18, "no_stoploss": 15, "overtrader": 60,
              "blown_account": 22}.get(profile, 12)
    rows = []
    base = since
    for i in range(counts):
        symbol = random.choice(symbols)
        direction = random.choice(["buy", "sell"])
        open_t = base + timedelta(hours=i * random.uniform(0.2, 1.0))
        hold = random.uniform(2, 25) if profile == "overtrader" else random.uniform(60, 1440)
        close_t = open_t + timedelta(minutes=hold)
        open_price = round(random.uniform(1.0, 2000.0), 2)
        volume = round(random.uniform(0.05, 1.0), 2)

        if profile == "disciplined":
            sl_set, win = True, random.random() < 0.55
        elif profile == "no_stoploss":
            sl_set, win = random.random() < 0.2, random.random() < 0.45
        elif profile == "overtrader":
            sl_set, win = random.random() < 0.6, random.random() < 0.42
        elif profile == "blown_account":
            sl_set, win = random.random() < 0.3, random.random() < 0.30
        else:
            sl_set, win = True, random.random() < 0.5

        if win:
            profit = round(random.uniform(20, 300) * volume, 2)
            tp_hit, sl_hit = random.random() < 0.6, False
        else:
            mult = random.uniform(2.0, 5.0) if not sl_set else 1.0
            if profile == "blown_account":
                mult *= random.uniform(1.5, 3.0)
            profit = round(-random.uniform(20, 250) * volume * mult, 2)
            tp_hit, sl_hit = False, sl_set and random.random() < 0.7

        sl_price = round(open_price * (0.98 if direction == "buy" else 1.02), 2) if sl_set else 0
        tp_price = round(open_price * (1.02 if direction == "buy" else 0.98), 2)
        close_price = round(open_price * (1.01 if win else 0.99), 2)
        rows.append({
            "login": login, "ticket": 700000 + login * 100 + i, "symbol": symbol,
            "type": direction, "volume": volume, "open_price": open_price,
            "close_price": close_price, "sl": sl_price, "tp": tp_price,
            "sl_hit": int(sl_hit), "tp_hit": int(tp_hit),
            "open_time": open_t.strftime("%Y-%m-%d %H:%M:%S"),
            "close_time": close_t.strftime("%Y-%m-%d %H:%M:%S"),
            "profit": profit,
        })
    return rows


def get_data_source(config):
    # Legacy switch wins for backwards-compat.
    if config.USE_MOCK_DATA:
        return MockMT5DataSource(config)
    source = config.DATA_SOURCE
    if source == "mock":
        return MockMT5DataSource(config)
    if source == "file":
        from .file_source import FileDataSource
        return FileDataSource(config)
    if source == "mt5":
        return RealMT5DataSource(config)
    if source == "brokeret":
        from .brokeret_source import BrokeretDataSource
        return BrokeretDataSource(config)
    raise ValueError(f"Unknown DATA_SOURCE: {source!r} "
                     f"(use file | brokeret | mt5 | mock)")
