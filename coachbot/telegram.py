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


MAX_LEN = 3900  # Telegram hard limit is 4096; leave headroom


def _chunks(text, limit=MAX_LEN):
    """Split long text into <=limit pieces, preferring paragraph breaks,
    then line breaks, then a hard cut. Order preserved."""
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for para in text.split("\n\n"):
        candidate = (buf + "\n\n" + para) if buf else para
        if len(candidate) <= limit:
            buf = candidate
            continue
        if buf:
            parts.append(buf)
            buf = ""
        if len(para) <= limit:
            buf = para
            continue
        # paragraph itself too long: split on lines, then hard-cut
        line_buf = ""
        for line in para.split("\n"):
            cand = (line_buf + "\n" + line) if line_buf else line
            if len(cand) <= limit:
                line_buf = cand
            else:
                if line_buf:
                    parts.append(line_buf)
                while len(line) > limit:
                    parts.append(line[:limit])
                    line = line[limit:]
                line_buf = line
        if line_buf:
            buf = line_buf
    if buf:
        parts.append(buf)
    return parts


def send_message(token, chat_id, text, dry_run=True):
    if dry_run:
        return {"status": "DRY_RUN", "chat_id": chat_id,
                "parts": len(_chunks(text))}
    pieces = _chunks(text)
    sent_ids = []
    for i, piece in enumerate(pieces, 1):
        resp = _post(token, "sendMessage", {"chat_id": chat_id, "text": piece,
                                            "parse_mode": "HTML",
                                            "disable_web_page_preview": True})
        if (not resp.get("ok")) and "parse entities" in str(resp.get("body", "")).lower():
            # malformed HTML from the model: deliver as plain text instead
            resp = _post(token, "sendMessage", {"chat_id": chat_id, "text": piece,
                                                "disable_web_page_preview": True})
        if resp.get("ok"):
            sent_ids.append(resp["result"]["message_id"])
            continue
        detail = resp.get("body") or ""
        return {"status": "failed", "chat_id": chat_id,
                "error": f"{resp.get('error') or resp} {detail}".strip(),
                "failed_part": f"{i}/{len(pieces)}",
                "sent_message_ids": sent_ids}
    return {"status": "sent", "chat_id": chat_id,
            "message_id": sent_ids[-1] if sent_ids else None,
            "parts": len(pieces), "message_ids": sent_ids}
