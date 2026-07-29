"""
telegram.py — delivery via Telegram Bot API.

Requires each user's numeric chat_id OR an @username the user has started a chat
with. Telegram bots CANNOT message a user who has never started the bot — the
user must press Start first. We resolve @username -> chat_id where possible and
skip (queue) users we cannot reach.

DRY_RUN (default): logs what would be sent, sends nothing.
"""
import json
import urllib.request
import urllib.error

from .logger import get_logger

log = get_logger("telegram")
API = "https://api.telegram.org/bot{token}/{method}"


def _post(token, method, payload):
    url = API.format(token=token, method=method)
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}", "body": e.read().decode()[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_message(token, chat_id, text, dry_run=True):
    if dry_run:
        return {"status": "DRY_RUN", "chat_id": chat_id}
    resp = _post(token, "sendMessage", {"chat_id": chat_id, "text": text,
                                        "disable_web_page_preview": True})
    if resp.get("ok"):
        return {"status": "sent", "chat_id": chat_id,
                "message_id": resp["result"]["message_id"]}
    return {"status": "failed", "chat_id": chat_id, "error": resp.get("error") or resp}
