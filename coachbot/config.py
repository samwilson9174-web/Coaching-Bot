"""
config.py — central configuration, all from environment / .env
Nothing secret is hard-coded. Load with python-dotenv in development.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name, default="0"):
    return os.environ.get(name, default).strip() in ("1", "true", "True", "yes")


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def _float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


class Config:
    # ---- Claude ----
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")

    # ---- MT5 Manager API ----
    # The MT5 Manager API is a native gateway; we talk to it through a small
    # adapter (see mt5_source.py). These are the connection params.
    MT5_SERVER = os.environ.get("MT5_SERVER", "")          # e.g. 1.2.3.4:443
    MT5_MANAGER_LOGIN = _int("MT5_MANAGER_LOGIN", 0)
    MT5_MANAGER_PASSWORD = os.environ.get("MT5_MANAGER_PASSWORD", "")
    MT5_GROUPS = [g.strip() for g in os.environ.get("MT5_GROUPS", "").split(",") if g.strip()]

    # ---- Telegram ----
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

    # ---- Data source ----
    # DATA_SOURCE selects where trades come from:
    #   "file" : read a manually-exported Excel/CSV (no server needed)
    #   "mt5"  : live pull via MT5 Manager API (needs the binding + server access)
    #   "mock" : synthetic data for testing
    # USE_MOCK_DATA=1 is kept for backwards-compat and forces "mock".
    DATA_SOURCE = os.environ.get("DATA_SOURCE", "file").strip().lower()
    TRADES_FILE = os.environ.get("TRADES_FILE", "data/trades.csv")
    USERS_FILE = os.environ.get("USERS_FILE", "data/users.csv")

    # ---- Run behaviour ----
    LOOKBACK_DAYS = _int("LOOKBACK_DAYS", 1)               # daily = 1 day window
    SEND_FOR_REAL = _bool("SEND_FOR_REAL")                 # master send switch
    USE_MOCK_DATA = _bool("USE_MOCK_DATA")                 # legacy: forces mock source
    DRY_RUN = not SEND_FOR_REAL

    # ---- Scheduling ----
    RUN_AT = os.environ.get("RUN_AT", "08:00")             # HH:MM local, daily
    SCHEDULE_ENABLED = _bool("SCHEDULE_ENABLED", "1")

    # ---- Classifier thresholds (compliance-tunable) ----
    HUMAN_REVIEW_NET_LOSS = _float("HUMAN_REVIEW_NET_LOSS", -5000)
    HUMAN_REVIEW_DRAWDOWN = _float("HUMAN_REVIEW_DRAWDOWN", -5000)
    HUMAN_REVIEW_SINGLE_LOSS = _float("HUMAN_REVIEW_SINGLE_LOSS", -1500)
    SOFT_DRAWDOWN = _float("SOFT_DRAWDOWN", -1000)
    SOFT_SL_PCT = _float("SOFT_SL_PCT", 50)
    SOFT_RR = _float("SOFT_RR", 0.5)
    OVERTRADE_PER_DAY = _float("OVERTRADE_PER_DAY", 5)
    SCALP_HOLD_MIN = _float("SCALP_HOLD_MIN", 20)

    # ---- IB commission ----
    IBS_FILE = os.environ.get("IBS_FILE", "data/ibs.csv")
    IB_DEFAULT_RATE_PER_LOT = _float("IB_DEFAULT_RATE_PER_LOT", 5.0)
    # Per-tier default rate, e.g. "Ashigaru:3,Samurai:5,Hatamoto:6,Daimyo:7,Shogun:8"
    IB_TIER_RATES = {
        k.strip(): float(v) for k, v in
        (pair.split(":") for pair in os.environ.get("IB_TIER_RATES", "").split(",") if ":" in pair)
    }
    # B-Book revenue share (OFF by default — compliance gate before enabling)
    IB_BBOOK_SHARE_ENABLED = _bool("IB_BBOOK_SHARE_ENABLED")
    IB_BBOOK_SHARE_PCT = _float("IB_BBOOK_SHARE_PCT", 0.0)
    IB_REPORT_PATH = os.environ.get("IB_REPORT_PATH", "output/ib_commissions.jsonl")
    # Optional per-(ib_id, symbol_group) overrides set programmatically; left empty by default.
    IB_RATES_BY_SYMBOL_GROUP = {}

    # ---- Market context + trade reports ----
    MARKET_PROVIDER = os.environ.get("MARKET_PROVIDER", "mock").strip().lower()
    MARKET_API_KEY = os.environ.get("MARKET_API_KEY", "")
    REPORT_MAX_TRADES = _int("REPORT_MAX_TRADES", 8)

    # ---- Brokeret cloud source (DATA_SOURCE=brokeret) ----
    # Transport: http | sftp | ftp | s3  (only used when DATA_SOURCE=brokeret)
    BROKERET_TRANSPORT = os.environ.get("BROKERET_TRANSPORT", "http").strip().lower()
    BROKERET_LOCAL_DIR = os.environ.get("BROKERET_LOCAL_DIR", "data/brokeret")
    BROKERET_TRADES_REMOTE = os.environ.get("BROKERET_TRADES_REMOTE", "")
    BROKERET_USERS_REMOTE = os.environ.get("BROKERET_USERS_REMOTE", "")
    # http transport
    BROKERET_HTTP_TOKEN = os.environ.get("BROKERET_HTTP_TOKEN", "")
    BROKERET_HTTP_HEADER = os.environ.get("BROKERET_HTTP_HEADER", "")   # "X-Api-Key: abc123"
    # sftp / ftp transport
    BROKERET_HOST = os.environ.get("BROKERET_HOST", "")
    BROKERET_PORT = _int("BROKERET_PORT", 0)                            # 0 -> transport default
    BROKERET_USER = os.environ.get("BROKERET_USER", "")
    BROKERET_PASSWORD = os.environ.get("BROKERET_PASSWORD", "")
    BROKERET_SSH_KEY = os.environ.get("BROKERET_SSH_KEY", "")           # sftp key file path
    BROKERET_FTP_TLS = _bool("BROKERET_FTP_TLS")
    # s3 transport
    BROKERET_S3_BUCKET = os.environ.get("BROKERET_S3_BUCKET", "")
    BROKERET_S3_ENDPOINT = os.environ.get("BROKERET_S3_ENDPOINT", "")   # for R2/MinIO/Wasabi
    BROKERET_S3_REGION = os.environ.get("BROKERET_S3_REGION", "")
    BROKERET_S3_KEY = os.environ.get("BROKERET_S3_KEY", "")
    BROKERET_S3_SECRET = os.environ.get("BROKERET_S3_SECRET", "")
    # If the REST API returns JSON wrapped under a non-standard key, name it here.
    BROKERET_JSON_RECORDS_KEY = os.environ.get("BROKERET_JSON_RECORDS_KEY", "")

    # ---- Storage ----
    AUDIT_PATH = os.environ.get("AUDIT_PATH", "output/audit_log.jsonl")
    REVIEW_QUEUE_PATH = os.environ.get("REVIEW_QUEUE_PATH", "output/human_review_queue.jsonl")
    STATE_PATH = os.environ.get("STATE_PATH", "output/sent_state.json")

    # ---- Health server (Railway) ----
    PORT = _int("PORT", 8080)

    @classmethod
    def validate_for_send(cls):
        problems = []
        if not cls.ANTHROPIC_API_KEY:
            problems.append("ANTHROPIC_API_KEY missing (generation will use mock text)")
        if cls.SEND_FOR_REAL and not cls.TELEGRAM_BOT_TOKEN:
            problems.append("SEND_FOR_REAL=1 but TELEGRAM_BOT_TOKEN missing")
        source = "mock" if cls.USE_MOCK_DATA else cls.DATA_SOURCE
        if source == "mt5" and not cls.MT5_SERVER:
            problems.append("DATA_SOURCE=mt5 but MT5_SERVER missing")
        if source == "file":
            if not os.path.exists(cls.TRADES_FILE):
                problems.append(f"DATA_SOURCE=file but TRADES_FILE not found: {cls.TRADES_FILE}")
            if not os.path.exists(cls.USERS_FILE):
                problems.append(f"DATA_SOURCE=file but USERS_FILE not found: {cls.USERS_FILE}")
        return problems


config = Config()
