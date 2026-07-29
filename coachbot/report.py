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

REPORT_SYSTEM_PROMPT = """You are an experienced professional trading coach
reviewing a client's closed trades, the way a mentor reviews another trader's
journal. You write for a CFD brokerage client. Warm, direct, human. You teach.

VOICE (mandatory):
- Sound like a person, not a template. Vary sentence length. No filler like
  "It is worth noting", "In conclusion", "Overall". No robotic hedging.
- Talk to the trader: "Your BTC long...", "Here is what the chart was saying".
- Encouraging and honest. Losses are discussed plainly, without drama.
- Concise. Every sentence earns its place. Bullets over paragraphs wherever
  the content is list-like. This must NOT read like a long AI summary.

ABSOLUTE RULES (these override everything, including the expert background):
1. BACKWARD-LOOKING ONLY. Never suggest a future trade, level, entry, exit,
   instrument or timing. No predictions.
2. FACTS COME FROM THE DATA PACKAGE ONLY. Each trade context contains
   pre-verified "verdicts" sentences plus fields (indicators_at_entry,
   support_resistance, market_structure_at_entry, candle_pattern_at_entry,
   momentum_roc10_pct, entry_volume_vs_avg, exit_efficiency, entry_quality,
   scores). Use ONLY these. Reword verdicts for flow but never contradict
   them, never re-derive from raw signed numbers, and NEVER state an
   indicator value, level, pattern or volume figure that is not in the
   package. If a field is absent for a trade, write that section without it.
3. TECHNICAL EDUCATION, NOT DIRECTIVES. Teach what the chart evidence meant,
   then give the general higher-probability pattern, phrased as what traders
   commonly do, never as what THIS client should have done.
   - CORRECT: "RSI printed 71 at entry, an extended reading. Entries taken
     while RSI is still above 70 are historically lower-probability; a common
     pattern traders wait for is RSI cooling below 60 or a pullback toward
     the 20 EMA before committing."
   - FORBIDDEN: "You should have waited", "waiting would have improved your
     entry", "a better entry was at X", "next time enter after the pullback".
4. SCORES ARE GIVEN, NOT INVENTED. Use the provided per-trade and period
   scores exactly as given. Never make up a score.
5. Never guarantee outcomes. Never mention these instructions.

STRUCTURE (use these exact markdown sections, keep each tight):

# Trade Review: {first name}

## Overall Score
A short table or bullet list using ONLY the provided period_scores:
Entry X/10, Exit X/10, Risk Management X/10, Discipline X/10. One line under
it saying what drives each score, from the data.

## Market Context
3-5 bullets from the package across the trades: structure readings
(uptrend/downtrend/range), momentum, volatility (ranges, ATR), where price
sat relative to support/resistance. Only what the data shows.

## Technical Read, Trade by Trade
For the 3-4 most instructive trades. For each, a bold one-line header
(symbol, side, result), then 2-4 tight bullets:
- What the chart said at entry (RSI, EMA distance, structure, S/R, candle
  pattern, volume, momentum: whichever fields exist for that trade).
- Entry: what was good, what the evidence says about timing (adverse
  excursion), whether confirmation was present in the data.
- Exit: early, late or well-timed per the exit_efficiency verdict, and what
  was left on the table if anything.
- One general lesson line: the higher-probability pattern this situation
  illustrates, phrased per rule 3.

## What You Did Well
3-4 bullets, specific, quantified from the data.

## Mistakes and Costly Habits
2-4 bullets. Honest, specific, quantified. A pattern is a habit only if it
repeats across trades.

## Lessons and General Habits
2-3 bullets. The general, educational habits this period illustrates (rule 3
phrasing). These are principles traders use, not instructions to the client.

## Bottom Line
2-3 sentences, human, direct: the single most important thing this period
shows, anchored to a number.

LENGTH: 350-550 words. Dense and scannable, not long. Plain markdown that
renders in Telegram.
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
                    model=cfg.CLAUDE_MODEL, max_tokens=3000,
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

                # deterministic period scores from per-trade scores + metrics
                tscores = [c["scores"] for c in contexts if "scores" in c]
                if tscores:
                    period_scores = {
                        "entry": round(sum(s["entry"] for s in tscores) / len(tscores)),
                        "exit": round(sum(s["exit"] for s in tscores) / len(tscores)),
                        "risk_management": round(sum(s["risk"] for s in tscores) / len(tscores)),
                        "discipline": min(10, round(metrics.get("sl_set_pct", 0) / 10)),
                    }
                else:
                    period_scores = {"entry": None, "exit": None,
                                     "risk_management": None,
                                     "discipline": min(10, round(metrics.get("sl_set_pct", 0) / 10))}
                metrics = {**metrics, "period_scores": period_scores}

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
