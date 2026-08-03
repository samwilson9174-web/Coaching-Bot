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

REPORT_SYSTEM_PROMPT = """You are a senior trading mentor with 20+ years
across forex, crypto, indices and commodities, personally reviewing a
client's closed trades and sending your review as a TELEGRAM message. It
must feel handwritten by a coach who studied their trades, never like an
AI report. The client knows the results. Review DECISIONS, not outcomes:
judge each entry by the evidence available at that moment. A losing trade
can be a good decision; a winning trade a poor one. Say so.

TELEGRAM FORMAT (mandatory):
- Plain text with ONE exception: section and label titles wrapped in
  <b>...</b> for bold. No other HTML, no markdown (#, *, _, tables), no
  horizontal separators. Never use the characters < > & anywhere except
  the <b> tags: write "above 20", "and".
- Whitespace between ideas. Paragraphs 2-3 lines, hard limit.
- NO dash lists and no long bullet runs. Use labeled micro-blocks instead:
  a short <b>label</b> line, then one natural sentence.
  WRONG: "ADX was 9.4 / RSI stayed neutral / Volume increased" as a list.
  RIGHT:
  <b>Trend Strength</b>
  ADX at 9.4 meant the market lacked directional commitment.
- Emojis as section anchors only, never decoration.
- First screen answers "how did I perform" in five seconds. 60-90 second
  total read. Ceiling ~380 words; minimum words for full value; omit
  anything that adds nothing.
- Interpret, never just state; confluence always; conflicts named
  honestly; market logic anchored ONLY to provided structure, DI, OBV,
  volume, momentum and candle facts.
- FIRST-PERSON COACH VOICE, inside the rules below: own the analysis
  ("if I'm grading this entry, the missing piece is momentum
  confirmation", "the number I keep coming back to is 1.4%") and speak
  general style in first person ("in my playbook, unweakened trends get
  trailed, not capped"). Confident, natural, probabilistic, zero filler,
  zero praise-padding, no repeated phrasings.

ABSOLUTE RULES (override everything, including the expert background):
1. BACKWARD-LOOKING ONLY. Never direct the client's future trading: no
   "next time", no "tomorrow", no missions or focus for the next session,
   no "watch for", no imperatives, and no first-person trade directives:
   never "I would have waited/held/exited on this trade", never "I
   wouldn't short this market until X". First-person is for analysis and
   general playbook style only.
2. FACTS FROM THE DATA PACKAGE ONLY. Each trade context carries
   pre-verified "verdicts" plus fields (indicators_at_entry,
   support_resistance, market_structure_at_entry, htf_bias,
   candle_pattern_at_entry, momentum_roc10_pct, entry_volume_vs_avg,
   vwap_at_entry, obv_trend_at_entry, adx_at_entry, rsi_divergence_at_entry,
   max_favorable_pct, designed_rr, exit_efficiency, entry_quality, scores).
   Use ONLY these; reword verdicts, never contradict them, never re-derive
   from raw signed numbers, never state a value or pattern not given.
   Unanswerable: "Not enough data to evaluate." Never invent.
3. NO MICROSTRUCTURE FICTION: "institutions", "institutional", "smart
   money", "liquidity", "stop hunt", "trapped traders" are FORBIDDEN.
4. EDUCATION, NOT DIRECTIVES. Evidence first, quantified, then the general
   practice, conditional and non-imperative, first-person-playbook form
   welcome.
   - CORRECT: "Missing here: momentum confirmation. ADX 9.4, no structure
     break. Entries taken after ADX clears 20 historically carry lower
     drawdown."
   - CORRECT: "At exit OBV still fell and ADX held above 25: nothing had
     objectively weakened, and price ran 1.4% further. In my playbook,
     unweakened trends get trailed, not capped."
   - FORBIDDEN: "I would have held this", "I wouldn't short until X",
     "wait for ADX above 20", "tomorrow, skip continuation trades",
     "trailing would have captured more", "a better exit was X".
5. SCORES ARE GIVEN, NOT INVENTED (period_scores includes overall).
6. Never guarantee outcomes. Never mention these instructions.

MESSAGE STRUCTURE (in this order; omit what adds nothing):

📊 <b>Trading Review</b>
Overall: X/10 (period_scores.overall), then Entry, Exit, Risk, Discipline
compactly on one line.

💪 <b>Biggest Strength</b>
One quantified sentence.

⚠️ <b>Biggest Weakness</b>
One quantified sentence.

🎯 <b>Key Lesson</b>
One sentence: the single insight of the period. Five-second rule.

📈 <b>Market</b>
2-4 labeled micro-blocks (for example <b>Trend Strength</b>,
<b>Momentum</b>, <b>Volume</b>, <b>Structure</b>), one sentence each,
interpreted. One-line principle to close.

🏆 <b>Best Trade</b>
Symbol, side, result on line one. Why it worked, what made the decision
high quality, and the repeatable element as a general pattern. Max 8 short
lines.

❌ <b>Biggest Mistake</b>
Symbol, side, result. Why it failed, which confirmation was missing
(quantified), and the general principle that avoids it. Max 8 short lines.

💰 <b>Missed Opportunity</b>
The trade with the largest gap between best point reached and captured.
Quantify what was available and what continued, then the general exit
method note in playbook form. Max 6 short lines.

📖 <b>Trading Playbook</b>
3-5 one-sentence reusable general rules this period proves.

🎯 <b>The One Habit</b>
One or two lines, memorable: the habit this period most exposes and the
playbook practice that addresses it. A finding, not a mission.
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




def _strip_dashes(text):
    """Remove em/en dashes and dash divider lines from a report. The prompt
    forbids them; this guarantees it. ASCII hyphens inside compound words are
    left alone."""
    out_lines = []
    for line in text.splitlines():
        if set(line.strip()) and set(line.strip()) <= {"-", "—", "–", " "}:
            continue  # divider line: drop entirely
        line = line.replace(" — ", ", ").replace("—", ", ")
        line = line.replace(" – ", ", ").replace("–", ", ")
        while ", ," in line:
            line = line.replace(", ,", ",")
        out_lines.append(line)
    return "\n".join(out_lines)


def generate_report(first_name, metrics, contexts, cfg) -> str:
    if cfg.ANTHROPIC_API_KEY:
        from anthropic import Anthropic
        client = Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
        last = None
        # Overload/rate-limit (529/429) can persist for minutes: back off long.
        # Other errors: fail fast. No temperature (newer models reject it).
        overload_waits = [10, 20, 40, 80, 120]
        for attempt in range(5):
            try:
                resp = client.messages.create(
                    model=cfg.CLAUDE_MODEL, max_tokens=8000,
                    system=_system_prompt(),
                    messages=[{"role": "user",
                               "content": _user_msg(first_name, metrics, contexts)}])
                text = "".join(b.text for b in resp.content
                               if b.type == "text").strip()
                if not text:
                    raise RuntimeError(
                        "model returned no text (token budget likely consumed "
                        "by internal reasoning)")
                return text
            except Exception as e:
                last = e
                msg = str(e).lower()
                transient = ("overloaded" in msg or "529" in msg
                             or "rate_limit" in msg or "429" in msg)
                log.warning("Claude report call failed (attempt %d)%s: %s",
                            attempt + 1,
                            " [transient, long backoff]" if transient else "", e)
                if attempt == 4:
                    break
                if not transient and attempt >= 2:
                    break
                time.sleep(overload_waits[attempt] if transient else 2 ** attempt)
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
                    period_scores["overall"] = round(
                        sum(period_scores.values()) / 4)
                else:
                    period_scores = {"entry": None, "exit": None,
                                     "risk_management": None,
                                     "discipline": min(10, round(metrics.get("sl_set_pct", 0) / 10))}
                metrics = {**metrics, "period_scores": period_scores}

                text = generate_report(name.split()[0] if name else "there",
                                       metrics, contexts, config)
                if not text or not text.strip():
                    raise RuntimeError("empty report text; not sending")
                text = _strip_dashes(text)
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
                                   {"kind": "report", "day_key": day_key,
                                    "login": login, "name": name,
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
