"""
dashboard.py — read-only operator dashboard for the coaching bot.
Covers every module: coach messages, trade reports, human review, IB
commissions. Reads the JSONL files the bot writes to output/. It never
sends anything and never runs the pipeline.

Run locally:  streamlit run dashboard.py
On Railway:   second service, same repo, sharing the output volume.
"""
import json
import os

import streamlit as st

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
AUDIT = os.path.join(OUTPUT_DIR, "audit_log.jsonl")
REVIEW = os.path.join(OUTPUT_DIR, "human_review_queue.jsonl")
IB = os.path.join(OUTPUT_DIR, "ib_commissions.jsonl")


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


def rec_kind(r):
    return r.get("kind") or "coach"


def rec_day(r):
    dk = r.get("day_key") or ""
    if ":" in dk:
        dk = dk.split(":", 1)[1]
    return dk or (r.get("logged_at") or "")[:10]


def delivery_status(r):
    return (r.get("delivery") or {}).get("status", "?")


st.set_page_config(page_title="Coaching bot — operator dashboard", layout="wide")
st.title("Coaching bot — operator dashboard")
st.caption("Read-only. Shows what the bot did. Nothing is sent from here.")

audit = read_jsonl(AUDIT)
review = read_jsonl(REVIEW)
ib = read_jsonl(IB)

tab_over, tab_msgs, tab_review, tab_ib = st.tabs(
    ["Overview", "Messages", "Human review", "IB commissions"])

# ---- Overview -------------------------------------------------------------
with tab_over:
    if not audit:
        st.info("No runs recorded yet. Run the coach or report pipeline first.")
    kinds = sorted({rec_kind(r) for r in audit}) or []
    for kind in kinds:
        recs = [r for r in audit if rec_kind(r) == kind]
        day = max((rec_day(r) for r in recs), default="")
        today = [r for r in recs if rec_day(r) == day]
        live = any(not r.get("dry_run", True) for r in today)
        st.subheader(f"{kind.title()} — latest run {day or '?'}")
        if live:
            st.error("Mode: LIVE — messages were really sent")
        else:
            st.warning("Mode: DRY-RUN — nothing sent")
        sent_real = sum(1 for r in today if delivery_status(r) == "sent")
        drafted = sum(1 for r in today if delivery_status(r) == "DRY_RUN")
        failed = sum(1 for r in today
                     if delivery_status(r) not in ("sent", "DRY_RUN"))
        blocked = sum(1 for r in today if not r.get("compliance_passed", True))
        c = st.columns(5)
        c[0].metric("Delivered", sent_real)
        c[1].metric("Drafted (dry)", drafted)
        c[2].metric("Failed", failed)
        c[3].metric("Filter blocked", blocked)
        c[4].metric("Recipients in run", len({r.get("login") for r in today}))
        rows = [{"login": r.get("login"), "name": r.get("name"),
                 "track": r.get("track", ""),
                 "status": delivery_status(r),
                 "parts": (r.get("delivery") or {}).get("parts", "")}
                for r in today]
        if rows:
            st.dataframe(rows, width="stretch", hide_index=True)
        st.divider()
    st.metric("Human review queue (all time)", len(review))

# ---- Messages -------------------------------------------------------------
with tab_msgs:
    st.subheader("All generated messages")
    st.caption("Newest first. This is the audit trail.")
    kind_opts = sorted({rec_kind(r) for r in audit})
    k_pick = st.multiselect("Kind", kind_opts, default=kind_opts)
    stat_opts = sorted({delivery_status(r) for r in audit})
    s_pick = st.multiselect("Delivery status", stat_opts, default=stat_opts)
    shown = [r for r in reversed(audit)
             if rec_kind(r) in k_pick and delivery_status(r) in s_pick]
    st.write(f"{len(shown)} messages")
    for r in shown:
        head = (f"{r.get('name','?')} · {rec_kind(r)} · "
                f"{delivery_status(r)} · {rec_day(r)}")
        with st.expander(head):
            if not r.get("compliance_passed", True):
                st.error("Blocked by compliance filter")
            st.text(r.get("message", "(no message)"))

# ---- Human review ---------------------------------------------------------
with tab_review:
    st.subheader("Human review queue")
    st.caption("Clients routed away from automated messaging, plus blocked "
               "or unreachable report cases. These need a person.")
    if not review:
        st.success("Queue is empty.")
    for r in review:
        label = r.get("kind") or "coach"
        with st.container(border=True):
            top = st.columns([3, 2])
            top[0].markdown(f"**{r.get('name','?')}** · login {r.get('login','?')}"
                            f" · {label}")
            top[1].markdown(str(r.get("routed_reason")
                                or r.get("reasons") or r.get("violations")
                                or "—"))
            m = r.get("metrics") or {}
            if m:
                mc = st.columns(4)
                mc[0].metric("Trades", m.get("num_trades", "—"))
                mc[1].metric("Net P/L", m.get("total_pl", "—"))
                mc[2].metric("Win %", m.get("win_rate_pct", "—"))
                mc[3].metric("Worst loss", m.get("worst_single_loss", "—"))

# ---- IB commissions -------------------------------------------------------
with tab_ib:
    st.subheader("IB commissions")
    if not ib:
        st.info("No IB run recorded. Run: python -m coachbot.main ib")
    else:
        day = max((r.get("day_key", "") for r in ib), default="")
        rows = [r for r in ib if r.get("day_key") == day] or ib
        st.caption(f"Latest run: {day} · lookback "
                   f"{rows[0].get('lookback_days','?')} days")
        total = sum(r.get("total_commission", 0) for r in rows)
        c = st.columns(3)
        c[0].metric("Total payout", f"${total:,.2f}")
        c[1].metric("IBs with activity",
                    sum(1 for r in rows if r.get("total_commission", 0) > 0))
        c[2].metric("IBs total", len(rows))
        table = [{"IB": r.get("name"), "tier": r.get("tier"),
                  "direct $": round(r.get("direct_commission", 0), 2),
                  "downline $": round(r.get("downline_commission", 0), 2),
                  "total $": round(r.get("total_commission", 0), 2),
                  "clients": r.get("client_count", 0)}
                 for r in sorted(rows, key=lambda x: x.get("total_commission", 0),
                                 reverse=True)]
        st.dataframe(table, width="stretch", hide_index=True)
