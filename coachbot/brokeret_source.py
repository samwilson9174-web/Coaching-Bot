"""
brokeret_source.py — FETCH-THEN-DELEGATE data source.
------------------------------------------------------
Pulls the daily trade + client export FROM Brokeret cloud storage, lands it as
local CSV/XLSX files, then hands off to the existing FileDataSource. Everything
downstream (analysis, classifier, Claude, compliance, telegram) is unchanged —
this only changes WHERE the file comes from, never WHO analyses it.

    Brokeret cloud  ->  [download to data/]  ->  FileDataSource  ->  pipeline

The fetch transport is configurable because how Brokeret exposes the file isn't
finalised yet. Set BROKERET_TRANSPORT in .env to one of:

  http   : GET a URL (optional Bearer token / header auth). Works for a REST
           endpoint OR a plain pre-signed download link.
  sftp   : pull over SFTP (paramiko).
  ftp    : pull over FTP/FTPS (stdlib ftplib).
  s3     : S3-compatible object storage (boto3) — also covers MinIO / R2 /
           Wasabi via BROKERET_S3_ENDPOINT.

Only the transport you actually use needs its extra dependency installed
(paramiko for sftp, boto3 for s3); http/ftp use the stdlib.

Two remote objects are fetched: the trades export and the clients export. If
Brokeret only produces ONE combined file, set BROKERET_USERS_REMOTE empty and
point USERS_FILE at a static local client list instead.

SAFETY / IDEMPOTENCY
- Files land under BROKERET_LOCAL_DIR with the run date in the name, so each
  day's raw pull is retained for audit ("what did we send on the 3rd?").
- We NEVER delete or write back to Brokeret — read-only pull.
- A checksum is logged so you can prove the file the pipeline saw.
"""
from __future__ import annotations
import os
import hashlib
from datetime import datetime, timezone

from .logger import get_logger

log = get_logger("brokeret")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _dated_local_path(local_dir: str, remote_name: str) -> str:
    os.makedirs(local_dir, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = os.path.basename(remote_name) or "export.csv"
    stem, ext = os.path.splitext(base)
    return os.path.join(local_dir, f"{stem}_{day}{ext or '.csv'}")


# --- transports -------------------------------------------------------------
def _fetch_http(remote: str, dest: str, config) -> None:
    import urllib.request
    req = urllib.request.Request(remote)
    token = getattr(config, "BROKERET_HTTP_TOKEN", "")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    extra = getattr(config, "BROKERET_HTTP_HEADER", "")
    if extra and ":" in extra:                       # "X-Api-Key: abc123"
        k, v = extra.split(":", 1)
        req.add_header(k.strip(), v.strip())
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        out.write(resp.read())


def _fetch_ftp(remote: str, dest: str, config) -> None:
    from ftplib import FTP, FTP_TLS
    host = config.BROKERET_HOST
    port = int(getattr(config, "BROKERET_PORT", 21) or 21)
    user = getattr(config, "BROKERET_USER", "")
    pwd = getattr(config, "BROKERET_PASSWORD", "")
    use_tls = getattr(config, "BROKERET_FTP_TLS", False)
    ftp = FTP_TLS() if use_tls else FTP()
    ftp.connect(host, port, timeout=60)
    ftp.login(user, pwd)
    if use_tls:
        ftp.prot_p()
    try:
        with open(dest, "wb") as out:
            ftp.retrbinary(f"RETR {remote}", out.write)
    finally:
        ftp.quit()


def _fetch_sftp(remote: str, dest: str, config) -> None:
    try:
        import paramiko
    except ImportError as e:
        raise RuntimeError("BROKERET_TRANSPORT=sftp requires paramiko "
                           "(pip install paramiko)") from e
    host = config.BROKERET_HOST
    port = int(getattr(config, "BROKERET_PORT", 22) or 22)
    user = config.BROKERET_USER
    pwd = getattr(config, "BROKERET_PASSWORD", "") or None
    key_path = getattr(config, "BROKERET_SSH_KEY", "") or None
    transport = paramiko.Transport((host, port))
    if key_path:
        pkey = paramiko.RSAKey.from_private_key_file(key_path)
        transport.connect(username=user, pkey=pkey)
    else:
        transport.connect(username=user, password=pwd)
    try:
        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.get(remote, dest)
    finally:
        transport.close()


def _fetch_s3(remote: str, dest: str, config) -> None:
    try:
        import boto3
    except ImportError as e:
        raise RuntimeError("BROKERET_TRANSPORT=s3 requires boto3 "
                           "(pip install boto3)") from e
    kwargs = {}
    endpoint = getattr(config, "BROKERET_S3_ENDPOINT", "")
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if getattr(config, "BROKERET_S3_KEY", ""):
        kwargs["aws_access_key_id"] = config.BROKERET_S3_KEY
        kwargs["aws_secret_access_key"] = config.BROKERET_S3_SECRET
    if getattr(config, "BROKERET_S3_REGION", ""):
        kwargs["region_name"] = config.BROKERET_S3_REGION
    s3 = boto3.client("s3", **kwargs)
    s3.download_file(config.BROKERET_S3_BUCKET, remote, dest)


_TRANSPORTS = {
    "http": _fetch_http,
    "https": _fetch_http,
    "ftp": _fetch_ftp,
    "ftps": _fetch_ftp,
    "sftp": _fetch_sftp,
    "s3": _fetch_s3,
}


def _fetch_one(remote: str, config) -> str:
    transport = str(getattr(config, "BROKERET_TRANSPORT", "http")).strip().lower()
    fn = _TRANSPORTS.get(transport)
    if fn is None:
        raise ValueError(f"Unknown BROKERET_TRANSPORT {transport!r} "
                         f"(use http | sftp | ftp | s3)")
    dest = _dated_local_path(config.BROKERET_LOCAL_DIR, remote)
    log.info("Fetching %s via %s -> %s", remote, transport, dest)
    fn(remote, dest, config)
    if not os.path.exists(dest) or os.path.getsize(dest) == 0:
        raise RuntimeError(f"Fetched file is missing or empty: {dest}")
    log.info("Fetched %s (%d bytes, sha256:%s)", os.path.basename(dest),
             os.path.getsize(dest), _sha256(dest))
    dest = _json_to_csv_if_json(dest)
    return dest


# --- JSON payload support ---------------------------------------------------
# REST APIs often return JSON records instead of a CSV file. If the fetched
# payload is JSON, flatten it to CSV (keys -> headers) so FileDataSource's
# alias machinery handles column naming exactly as it does for exports.
# Handles: a bare array of objects, or an object wrapping the array under a
# common key (data/result/results/items/records/trades/deals/clients/users),
# with BROKERET_JSON_RECORDS_KEY as an explicit override.
_WRAPPER_KEYS = ("data", "result", "results", "items", "records",
                 "trades", "deals", "history", "clients", "users", "list")


def _json_to_csv_if_json(path: str) -> str:
    import csv as _csv
    import json as _json
    with open(path, "rb") as f:
        head = f.read(64).lstrip()
    if not head[:1] in (b"[", b"{"):
        return path                                   # not JSON — CSV/XLSX as-is
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            payload = _json.load(f)
    except Exception:
        return path                                   # looked like JSON but isn't

    from .config import config as _cfg
    records = None
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        override = getattr(_cfg, "BROKERET_JSON_RECORDS_KEY", "")
        keys = ([override] if override else []) + list(_WRAPPER_KEYS)
        for k in keys:
            v = payload.get(k)
            if isinstance(v, list):
                records = v
                break
    if not records or not isinstance(records[0], dict):
        raise RuntimeError(
            f"JSON payload in {os.path.basename(path)} has no recognisable record "
            f"array. Set BROKERET_JSON_RECORDS_KEY to the key holding the list. "
            f"Top-level keys: {list(payload)[:8] if isinstance(payload, dict) else type(payload)}")

    # union of keys across records, first-seen order; one level flatten
    def _flat(r):
        out = {}
        for k, v in r.items():
            if isinstance(v, dict):
                for k2, v2 in v.items():
                    out[f"{k}_{k2}"] = v2
            elif isinstance(v, list):
                out[k] = _json.dumps(v)
            else:
                out[k] = v
        return out

    flats = [_flat(r) for r in records]
    header, seen = [], set()
    for r in flats:
        for k in r:
            if k not in seen:
                seen.add(k)
                header.append(k)

    csv_path = os.path.splitext(path)[0] + ".converted.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(flats)
    log.info("JSON payload converted: %d records -> %s",
             len(flats), os.path.basename(csv_path))
    return csv_path


class BrokeretDataSource:
    """
    Fetches remote export(s), then delegates to FileDataSource. Presents the
    exact same interface (connect / disconnect / get_users / get_closed_deals),
    so the factory and pipeline treat it identically to the file source.
    """
    def __init__(self, config):
        self.config = config
        self._delegate = None

    def connect(self):
        cfg = self.config

        trades_remote = cfg.BROKERET_TRADES_REMOTE
        if not trades_remote:
            raise ValueError("DATA_SOURCE=brokeret but BROKERET_TRADES_REMOTE is empty")
        trades_local = _fetch_one(trades_remote, cfg)

        users_remote = getattr(cfg, "BROKERET_USERS_REMOTE", "")
        if users_remote:
            users_local = _fetch_one(users_remote, cfg)
        else:
            users_local = cfg.USERS_FILE          # fall back to a static local client list
            log.info("No BROKERET_USERS_REMOTE set — using local USERS_FILE %s", users_local)

        # Repoint the file source at what we just downloaded, then delegate.
        cfg.TRADES_FILE = trades_local
        cfg.USERS_FILE = users_local
        from .file_source import FileDataSource
        self._delegate = FileDataSource(cfg)
        self._delegate.connect()

    def disconnect(self):
        if self._delegate:
            self._delegate.disconnect()

    def get_users(self):
        return self._delegate.get_users()

    def get_closed_deals(self, login, since):
        return self._delegate.get_closed_deals(login, since)
