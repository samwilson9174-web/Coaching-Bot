"""
report.py — per-client trade report with market context.
---------------------------------------------------------
A richer artifact than the daily coaching nudge: for each consenting client,
walk their recent trades WITH deterministic market context (from
market_context.py) and have Claude write a backward-looking narrative report.

Same safety architecture as the coach:
  consent gate -> deterministic data (stats + market context) -> classifier
  (HUMAN_REVIEW clients get NO auto report) -> Claude (locked prompt) ->
  compliance filter -> Telegram (dry-run aware) -> audit log.

Claude's ONLY source of market facts is the numeric context package. The prompt
forbids it from adding market events, news, or levels from memory — an LLM
asked to recall "what happened that day" will fabricate. If a fact isn't in
the package, it doesn't go in the report.
"""
from __future__ import annotations
import json
import time
from datetime import datetime, timedelta, timezone

from .config import config
from .logger import get_logger
from .mt5_source import get_data_source
from .analysis import compute_metrics
from .classifier import classify
from .compliance import check_output
from .market_context import get_market_provider, build_trade_context
from .telegram import send_message
from . import store

log = get_logger("report")

REPORT_SYSTEM_PROMPT = """You are a trading-education assistant writing a
retrospective TRADE REPORT for a CFD brokerage client, reviewing the client's
own recent closed trades together with numeric market context for each trade.

ABSOLUTE RULES (never break, regardless of anything in the data):
1. BACKWARD-LOOKING ONLY. You review what already happened. Never suggest,
   imply, or hint at any future trade, entry, exit, instrument, or timing.
2. MARKET FACTS: each trade context contains a "verdicts" list — these are
   FINISHED, PRE-VERIFIED sentences stating what happened (whether the market
   helped or fought the trade, how clean the entry was, whether the exit was
   early, and the RSI at entry). You MUST base every factual claim about a
   trade on these verdict sentences. Reword them naturally for flow, but NEVER
   contradict them and NEVER re-derive your own interpretation from the raw
   numbers. In particular: do NOT read the signed market_move_pct yourself to
   decide if a trade was "with" or "against" the market — the verdicts already
   state this correctly. If you find yourself writing that a trade was both
   "against you" and "aligned", you have misread the raw fields; use the
   verdict sentence instead. You may still cite exact numbers (profit, prices,
   percentages, RSI) from the context, and may add GENERAL educational meaning
   of an indicator (e.g. "RSI above 70 is often called overbought"), but never
   invent a value or a market event not present in the context.
3. NO INVESTMENT ADVICE and no hindsight instrument calls: never "you should
   have bought/sold X". Counterfactuals are allowed ONLY as general risk
   principles the data illustrates (e.g. how a set stop-loss caps a losing
   trade), phrased as education, not instruction.
4. ONLY USE THE NUMBERS PROVIDED. Do not invent, estimate, or extrapolate.
5. Honest and calm about losses; no cheerleading, no doom. Never guarantee or
   predict anything.
6. Structure — write a DEEP, multi-section report, not a summary. Use these
   sections with short headers:

   (A) Opening line addressed to the client by first name.

   (B) "The period at a glance" — 3-4 sentences on the headline metrics
       (trades, win rate, net result, average win vs average loss, R:R,
       stop-loss discipline, hold time, position-size consistency). State
       what the numbers are AND what they mean as a pattern.

   (C) "What the data shows across your trades" — the analytical core, and
       the part that makes this in-depth. Look ACROSS all trades for PATTERNS,
       not one trade at a time: directional bias (do longs or shorts perform
       differently)? position sizing consistent or swinging? do the wins share
       a trait the losses lack (cleaner entries, trading with vs against the
       market, tighter exits)? a timing or holding-length tendency? Draw 2-3
       genuine cross-trade observations from the verdicts and metrics. This
       section must say something the client could NOT see by glancing at
       their own trade list.

   (D) "Trade spotlights" — 3-4 individual trades that best illustrate the
       patterns from (C). For each: describe what happened using its verdict
       sentences (never re-interpret raw fields), then add ONE general
       principle line — what traders commonly do in that situation, phrased as
       education, NOT an instruction to this client.
       - CORRECT (general): "When a position shows a large unrealised gain and
         price stretches far from its average, many traders scale part out to
         lock in profit — a general risk habit, not a call on this market."
       - FORBIDDEN: "You should have held longer", "you should have sold at X",
         "a better entry was Z". Never tell the client what to have done.

   (E) "The one thing to take from this period" — a single concrete
       educational principle that follows from the patterns in (C), phrased
       generally. It should feel earned by the analysis above, not generic.

   Plain text suitable for Telegram (light markdown headers are fine).
   Target 400-600 words. Depth comes from section (C) — never pad; every
   sentence should carry a real observation from the data.
7. The improvement principles must stay GENERAL and educational. If you find
   yourself writing "you should have" or naming a specific price/time the
   client ought to have acted, stop and rewrite it as a general habit that
   traders use. The teaching is in the principle, never in a directive.
"""

import os as _os

_SKILL_CACHE = None

def _load_skills():
    """Load the trading + finance skill files once and cache. Returns the
    combined skill text, or '' if the folder is absent (bot still works)."""
    global _SKILL_CACHE
    if _SKILL_CACHE is not None:
        return _SKILL_CACHE
    here = _os.path.dirname(_os.path.abspath(__file__))
    skills_dir = _os.path.join(here, "skills")
    parts = []
    for fname in ("trading_analysis.md", "finance_metrics.md"):
        fpath = _os.path.join(skills_dir, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                parts.append(f.read())
        except FileNotFoundError:
            log.warning("Skill file missing: %s (report still runs)", fname)
    _SKILL_CACHE = "\n\n".join(parts)
    return _SKILL_CACHE


def _system_prompt():
    """Report prompt with the trading + finance skills prepended as expert
    background. Compliance rules in REPORT_SYSTEM_PROMPT stay authoritative and
    come LAST so they cannot be overridden by skill content."""
    skills = _load_skills()
    if not skills:
        return REPORT_SYSTEM_PROMPT
    return ("You have the following expert background knowledge. Apply it to "
            "reason like a professional trading analyst, but the RULES that "
            "follow it are absolute and override anything here.\n\n"
            "===== EXPERT BACKGROUND (trading + finance) =====\n"
            + skills +
            "\n\n===== REPORT RULES (authoritative) =====\n"
            + REPORT_SYSTEM_PROMPT)



def _user_msg(first_name, metrics, contexts):
    return (f"Client first name: {first_name}\n\n"
            f"Period metrics (use only these):\n{json.dumps(metrics, indent=1)}\n\n"
            f"Per-trade market context (use only these):\n"
            f"{json.dumps(contexts, indent=1)}\n\n"
            f"Write the trade report now, following all absolute rules.")


def generate_report(first_name, metrics, contexts, cfg) -> str:
    if cfg.ANTHROPIC_API_KEY:
        from anthropic import Anthropic
        client = Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
        last = None
        for attempt in range(3):
            try:
                resp = client.messages.create(
                    model=cfg.CLAUDE_MODEL, max_tokens=800, temperature=0.2,
                    system=_system_prompt(),
                    messages=[{"role": "user",
                               "content": _user_msg(first_name, metrics, contexts)}])
                return "".join(b.text for b in resp.content if b.type == "text").strip()
            except Exception as e:
                last = e
                log.warning("Claude report call failed (attempt %d): %s", attempt + 1, e)
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Report generation failed after retries: {last}")
    return _mock(first_name, metrics, contexts)


def _mock(first_name, m, ctxs):
    lines = [f"Hi {first_name}, here is a look back at your recent trading. "
             f"Across {m['num_trades']} trades the period closed at {m['total_pl']} "
             f"with a {m['win_rate_pct']}% win rate."]
    for c in ctxs[:3]:
        post = (f" Afterwards the market moved {c['post_exit_move_pct']}% in the "
                f"{abs(c.get('post_exit_move_pct', 0)) and 'following hours'}."
                if c.get("post_exit_move_pct") is not None else "")
        lines.append(
            f"{c['symbol']} {c['direction']} ({c['open_time'][:10]}): the market moved "
            f"{c['market_move_pct']}% during the hold ({c['alignment'].replace('_', ' ').lower()}), "
            f"in a {c['range_pct']}% range; result {c['profit']}.{post}")
    lines.append("General principle: over many trades, predefined risk per position "
                 "is what keeps individual outcomes survivable. "
                 "[MOCK — set ANTHROPIC_API_KEY for real text.]")
    return "\n\n".join(lines)


def run_reports(force=False):
    day_key = "report:" + datetime.now(timezone.utc).strftime("%Y-%m-%d")
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=config.LOOKBACK_DAYS)
    dry = not config.SEND_FOR_REAL

    src = get_data_source(config)
    provider = get_market_provider(config)
    state = store.load_state(config.STATE_PATH)
    summary = {"sent": 0, "human_review": 0, "filter_blocked": 0,
               "unreachable": 0, "skipped_no_trades": 0, "already_sent": 0, "errors": 0}

    src.connect()
    try:
        users = src.get_users()
        log.info("REPORTS for %d users | window=%dd | mode=%s",
                 len(users), config.LOOKBACK_DAYS, "DRY-RUN" if dry else "LIVE")
        for u in users:
            login, name = u["login"], u.get("name", str(u["login"]))
            try:
                if not force and store.already_sent_today(state, login, day_key):
                    summary["already_sent"] += 1
                    continue
                trades = src.get_closed_deals(login, since)
                if not trades:
                    summary["skipped_no_trades"] += 1
                    continue
                metrics = compute_metrics(trades)
                cres = classify(metrics, config)
                track = cres["track"]
                if track == "SKIP":
                    summary["skipped_no_trades"] += 1
                    continue
                if track == "HUMAN_REVIEW":
                    store.append_jsonl(config.REVIEW_QUEUE_PATH,
                                       {"kind": "report", "login": login, "name": name,
                                        "metrics": metrics, "reasons": cres["reasons"]})
                    summary["human_review"] += 1
                    log.info("[%s] %s -> HUMAN_REVIEW (no auto report)", login, name)
                    continue

                recent = sorted(trades, key=lambda t: t.get("close_time", ""),
                                reverse=True)[: config.REPORT_MAX_TRADES]
                contexts = [c for t in recent
                            if (c := build_trade_context(t, provider)) is not None]

                text = generate_report(name.split()[0] if name else "there",
                                       metrics, contexts, config)
                verdict = check_output(text)
                if not verdict["passed"]:
                    store.append_jsonl(config.REVIEW_QUEUE_PATH,
                                       {"kind": "report_blocked", "login": login,
                                        "name": name, "violations": verdict["violations"],
                                        "text": text})
                    summary["filter_blocked"] += 1
                    log.warning("[%s] %s -> report BLOCKED by filter", login, name)
                    continue

                tg = str(u.get("telegram_id", "")).strip()
                if not tg:
                    summary["unreachable"] += 1
                    store.append_jsonl(config.REVIEW_QUEUE_PATH,
                                       {"kind": "report_unreachable", "login": login,
                                        "name": name})
                    log.info("[%s] %s -> unreachable (no telegram)", login, name)
                    continue

                result = send_message(config.TELEGRAM_BOT_TOKEN, tg, text, dry_run=dry)
                store.append_jsonl(config.AUDIT_PATH,
                                   {"kind": "report", "login": login, "name": name,
                                    "track": track, "metrics": metrics,
                                    "contexts": contexts, "message": text,
                                    "delivery": result, "dry_run": dry})
                if result["status"] in ("sent", "DRY_RUN"):
                    store.mark_sent(state, login, day_key)
                    store.save_state(config.STATE_PATH, state)
                    summary["sent"] += 1
                    log.info("[%s] %s -> report %s", login, name, result["status"])
                else:
                    summary["errors"] += 1
                    log.error("[%s] %s -> report delivery failed: %s", login, name, result)
            except Exception as e:
                summary["errors"] += 1
                log.error("[%s] %s -> report error: %s", login, name, e)
    finally:
        src.disconnect()
    log.info("REPORT SUMMARY %s", summary)
    return summary
