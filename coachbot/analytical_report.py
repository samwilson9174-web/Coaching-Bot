"""
analytical_report.py — turns deterministic findings into a written REPORT.
---------------------------------------------------------------------------
This is the report engine. It does two things the summary did not:
  1. It reasons from FINDINGS (analyse_trades output), not raw rows — so the
     output makes judgments (concentration, edge, execution) instead of
     restating numbers.
  2. Market events, when present, come ONLY from a verified events source
     (get_events) — never the model's memory. The prompt forbids the model
     from adding any market event, date, or cause not present in the events
     block. Dates with no verified events simply get no market-cause claims.

MARKET EVENTS — the fabrication boundary:
  get_events(dates) returns a list of {date, market, event} that a HUMAN or a
  real data source verified. This module ships it STUBBED (returns []), with
  two honest ways to fill it:
     - a curated file (events.csv: date,market,event)
     - a live fetch you explicitly enable
  Until one is wired, reports run WITHOUT market-cause claims — analytical on
  execution/concentration/edge, silent on 'why the market moved'. That is the
  correct default: no data, no claim.
"""
from __future__ import annotations
import csv
import json
import os
import time

from .logger import get_logger

log = get_logger("report")

REPORT_SYSTEM_PROMPT = """You are a senior trading analyst writing a
retrospective ANALYTICAL REPORT on a trader's own closed positions. You are
given (a) deterministic FINDINGS computed from their trades and (b) optionally
a VERIFIED EVENTS block of real market events on the relevant dates.

This is a REPORT, not a summary. A summary restates what happened; a report
reaches conclusions the reader cannot see by looking at their trade list.

ABSOLUTE RULES:
1. BACKWARD-LOOKING ONLY. Analyse what happened. Never suggest, imply, or hint
   at any future trade, entry, exit, instrument, price, or timing.
2. MARKET FACTS: you may reference a market event, its date, or its cause ONLY
   if it appears verbatim in the VERIFIED EVENTS block. If that block is empty
   or a date is not in it, you MUST NOT state or imply why the market moved on
   that date. Never supply an event, headline, level, or cause from your own
   knowledge. Silence is required where data is absent.
3. NO INVESTMENT ADVICE. You may identify what a pattern in the DATA suggests
   about the trader's process (e.g. 'exits captured only part of the move'),
   framed as observation, never as instruction to do X next time.
4. USE ONLY THE NUMBERS IN THE FINDINGS. Do not invent or estimate figures.
5. Be direct and analytical, including about weaknesses: concentration risk,
   thin edge relative to leverage, reliance on a few trades, one-instrument
   dependence. A flattering report is a failed report.

STRUCTURE (write in prose, ~350-500 words, plain text):
- Opening verdict: one honest sentence on what the result really is.
- How the profit was actually made: concentration, direction, instrument.
- Execution quality: move captured vs leverage risked; what it implies.
- Where the market context is verified, tie specific trades to real events
  from the EVENTS block — otherwise omit market causation entirely.
- What the data suggests could be examined (process observations, not trade
  tips), and a balanced closing note on risk. Past results do not predict
  future outcomes.
"""


# --- events hook: STUBBED. Returns verified events only, never invented. ----
def get_events(dates: list, config=None) -> list:
    """Return verified market events for the given dates. Ships empty.
    Fill via events.csv (date,market,event) or an explicit live fetch."""
    path = getattr(config, "EVENTS_FILE", "data/events.csv") if config else "data/events.csv"
    if not os.path.exists(path):
        return []
    want = {str(d)[:10] for d in dates}
    out = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if str(row.get("date", ""))[:10] in want:
                out.append({"date": row["date"], "market": row.get("market", ""),
                            "event": row.get("event", "")})
    log.info("Loaded %d verified events for %d dates", len(out), len(want))
    return out


def _user_message(name, findings, events):
    ev = ("VERIFIED EVENTS (the only market facts you may cite):\n"
          + json.dumps(events, indent=1)) if events else \
        ("VERIFIED EVENTS: none available for these dates. Do NOT state or "
         "imply any market cause or event. Analyse the trades only.")
    return (f"Trader: {name}\n\n"
            f"FINDINGS (deterministic, authoritative):\n{json.dumps(findings, indent=1)}\n\n"
            f"{ev}\n\nWrite the analytical report now, obeying every absolute rule.")


def generate_report(name, findings, events, config) -> str:
    if getattr(config, "ANTHROPIC_API_KEY", ""):
        from anthropic import Anthropic
        client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        last = None
        for attempt in range(3):
            try:
                resp = client.messages.create(
                    model=config.CLAUDE_MODEL, max_tokens=1200,
                    system=REPORT_SYSTEM_PROMPT,
                    messages=[{"role": "user",
                               "content": _user_message(name, findings, events)}])
                return "".join(b.text for b in resp.content if b.type == "text").strip()
            except Exception as e:
                last = e
                log.warning("Report call failed (attempt %d): %s", attempt + 1, e)
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Report generation failed: {last}")
    return _mock(name, findings, events)


def _mock(name, f, events):
    c = f["concentration"]; e = f["execution"]
    inst = max(f["by_instrument"].items(), key=lambda kv: kv[1]["net"])
    parts = [
        f"{name}, this report reviews {f['n']} closed positions totalling "
        f"{f['total_pnl']:+,} USDT.",
        f"How the profit was made: it is {c['verdict']} — just "
        f"{c['trades_for_half_profit']} of {f['n']} trades produced half the "
        f"result, and the two largest ({', '.join(c['top2_trades'])}) account "
        f"for {c['top2_share_pct']}% of it. {inst[0]} alone drove "
        f"{inst[1]['share_pct']}% of the total.",
        f"Execution: the average favourable move captured was "
        f"{e['avg_favorable_move_pct']}% while average leverage was "
        f"{e['avg_leverage']}x. Small underlying moves amplified by high "
        f"leverage means the edge per trade was thin and the risk carried was "
        f"large — a combination that works until one move runs the other way.",
    ]
    if events:
        parts.append("Verified market context was available for some dates and "
                     "is reflected where relevant.")
    else:
        parts.append("No verified market events were available for these dates, "
                     "so this report makes no claim about what moved the market.")
    parts.append("The concentration and the thin captured-move-versus-leverage "
                 "profile are the two things worth examining. Past results do "
                 "not predict future outcomes. [MOCK — set ANTHROPIC_API_KEY.]")
    return "\n\n".join(parts)
