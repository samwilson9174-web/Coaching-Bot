"""
dashboard.py — read-only operator dashboard for the coaching bot.
------------------------------------------------------------------
Reads the JSONL/JSON files the bot writes to output/. It does NOT run the
pipeline, call Claude, or send anything — it only displays what already
happened. Safe to run anywhere; shows real data once the bot runs against
Brokeret, dummy data before that. Nothing here depends on the data SOURCE.

Run locally:  streamlit run dashboard.py
On Railway:   separate service, same repo, sharing the output volume.
"""
import json
import os
from datetime import datetime

import streamlit as st

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
AUDIT = os.path.join(OUTPUT_DIR, "audit_log.jsonl")
REVIEW = os.path.join(OUTPUT_DIR, "human_review_queue.jsonl")
IB = os.path.join(OUTPUT_DIR, "ib_commissions.jsonl")
STATE = os.path.join(OUTPUT_DIR, "sent_state.json")


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def latest_day(records):
    days = [r.get("day_key", "") for r in records if r.get("day_key")]
    return max(days) if days else None


st.set_page_config(page_title="Coaching bot — operator dashboard", layout="wide")
st.title("Coaching bot — operator dashboard")
st.caption("Read-only view of what the bot has done. It does not send anything from here.")

audit = read_jsonl(AUDIT)
review = read_jsonl(REVIEW)
ib = read_jsonl(IB)

tab_run, tab_review, tab_ib, tab_messages = st.tabs(
    ["Daily run", "Human review", "IB commissions", "All messages"])

# ---- Daily run ------------------------------------------------------------
with tab_run:
    day = latest_day(audit)
    today = [r for r in audit if r.get("day_key") == day] if day else []
    live = any(not r.get("dry_run", True) for r in today)

    top = st.columns([2, 1])
    with top[0]:
        st.subheader(f"Latest run: {day or 'no runs yet'}")
    with top[1]:
        if not today:
            st.info("No run recorded yet.")
        elif live:
            st.error("Mode: LIVE — messages were really sent")
        else:
            st.warning("Mode: DRY-RUN — nothing sent")

    sent = sum(1 for r in today if r.get("delivery", {}).get("status") in ("sent", "DRY_RUN"))
    failed = sum(1 for r in today if r.get("delivery", {}).get("status") not in ("sent", "DRY_RUN"))
    blocked = sum(1 for r in today if not r.get("compliance_passed", True))
    hr = sum(1 for r in review if r.get("logged_at", "")[:10] == (day or "")[:10]) if day else len(review)

    c = st.columns(4)
    c[0].metric("Sent / drafted", sent)
    c[1].metric("Delivery failed", failed)
    c[2].metric("Filter blocked", blocked)
    c[3].metric("Human review", len(review))

    if today:
        st.markdown("##### Per-client outcome")
        rows = [{"login": r.get("login"), "name": r.get("name"),
                 "track": r.get("track"),
                 "compliance": "pass" if r.get("compliance_passed", True) else "BLOCKED",
                 "delivery": r.get("delivery", {}).get("status", "?")}
                for r in today]
        st.dataframe(rows, width='stretch', hide_index=True)

# ---- Human review ---------------------------------------------------------
with tab_review:
    st.subheader("Human review queue")
    st.caption("Clients routed away from automated messaging. These need a person, "
               "not the bot. The dashboard does not send to them.")
    if not review:
        st.success("Queue is empty.")
    for r in review:
        with st.container(border=True):
            head = st.columns([3, 2])
            head[0].markdown(f"**{r.get('name','?')}**  ·  login {r.get('login','?')}")
            head[1].markdown(f"reason: {r.get('routed_reason', r.get('reasons','—'))}")
            m = r.get("metrics", {})
            if m:
                mc = st.columns(4)
                mc[0].metric("Trades", m.get("num_trades", "—"))
                mc[1].metric("Net P/L", m.get("total_pl", "—"))
                mc[2].metric("Win %", m.get("win_rate_pct", "—"))
                mc[3].metric("Worst loss", m.get("worst_single_loss", "—"))

# ---- IB commissions -------------------------------------------------------
with tab_ib:
    st.subheader("IB commissions")
    day = latest_day(ib)
    rows = [r for r in ib if r.get("day_key") == day] if day else ib
    if not rows:
        st.info("No IB commission run recorded yet. Run: python -m coachbot.main ib")
    else:
        st.caption(f"Latest run: {day} · lookback {rows[0].get('lookback_days','?')} days")
        total = sum(r.get("total_commission", 0) for r in rows)
        active = sum(1 for r in rows if r.get("total_commission", 0) > 0)
        tc = st.columns(3)
        tc[0].metric("Total payout", f"${total:,.2f}")
        tc[1].metric("IBs with activity", active)
        tc[2].metric("IBs total", len(rows))
        table = [{"IB": r.get("name"), "tier": r.get("tier"),
                  "direct $": round(r.get("direct_commission", 0), 2),
                  "downline $": round(r.get("downline_commission", 0), 2),
                  "total $": round(r.get("total_commission", 0), 2),
                  "clients": r.get("client_count", 0)}
                 for r in sorted(rows, key=lambda x: x.get("total_commission", 0), reverse=True)]
        st.dataframe(table, width='stretch', hide_index=True)

# ---- All messages ---------------------------------------------------------
with tab_messages:
    st.subheader("All generated messages")
    st.caption("Every message the bot produced, newest first. This is the audit trail.")
    tracks = sorted({r.get("track", "?") for r in audit})
    pick = st.multiselect("Filter by track", tracks, default=tracks)
    shown = [r for r in reversed(audit) if r.get("track") in pick]
    st.write(f"{len(shown)} messages")
    for r in shown:
        d = r.get("delivery", {}).get("status", "?")
        with st.expander(f"{r.get('name','?')} · {r.get('track','?')} · {d} · {r.get('logged_at','')[:19]}"):
            if not r.get("compliance_passed", True):
                st.error("Blocked by compliance filter")
            st.write(r.get("message", "(no message)"))
