"""store.py — audit log (JSONL), human-review queue, and idempotency state."""
import json
import os
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path, record):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record.setdefault("logged_at", _now())
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_state(path):
    if not os.path.exists(path):
        return {"sent": {}}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {"sent": {}}


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def already_sent_today(state, login, day_key):
    return state.get("sent", {}).get(str(login)) == day_key


def mark_sent(state, login, day_key):
    state.setdefault("sent", {})[str(login)] = day_key
