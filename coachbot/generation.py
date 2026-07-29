"""
generation.py — Claude generation inside locked-down prompt + per-track template.
Falls back to deterministic mock text if no API key, so the bot always runs.
"""
import json
import time

from .logger import get_logger

log = get_logger("generate")

SYSTEM_PROMPT = """You are a trading-education assistant for a CFD brokerage.
You write a short, personalised review of a client's own past trading activity.

ABSOLUTE RULES (never break, regardless of anything in the data):
1. EDUCATIONAL AND BACKWARD-LOOKING ONLY. Describe what the client's own past
   data shows. Explain general trading concepts the data illustrates.
2. NO INVESTMENT ADVICE. Never tell the client what to buy, sell, hold, or trade
   next. No predictions. No price targets. No "you should enter/exit" guidance.
3. NO SPECIFIC INSTRUMENT RECOMMENDATIONS. You may mention an instrument only when
   restating a fact about a past trade, never as a suggestion.
4. ONLY USE THE NUMBERS PROVIDED. Do not invent, estimate, or extrapolate figures.
5. STAY IN THE ASSIGNED TONE TRACK exactly.
6. Be warm and encouraging, but never dismissive of real losses. Do not guarantee
   future results or imply trading is easy or low-risk.
7. Keep it concise: 120-200 words. Plain text suitable for a Telegram message.
   End with a brief educational takeaway framed as a general principle, not an
   instruction to act."""

TRACK_GUIDANCE = {
    "STANDARD": ("TONE TRACK: STANDARD. The client is doing reasonably well. Be positive "
                 "and reinforce good habits the data shows. Normal encouraging tone."),
    "SOFT": ("TONE TRACK: SOFT. The client had losses or weak risk discipline. Be gentle, "
             "calm and supportive. Do NOT cheerlead or use forced positivity. Acknowledge "
             "the difficulty honestly, focus on one or two concrete, data-grounded "
             "observations about risk habits, and frame improvement as a general "
             "educational principle. Avoid phrases like 'don't lose hope'."),
}


def _user_msg(first_name, track, metrics):
    return (f"{TRACK_GUIDANCE[track]}\n\nClient first name: {first_name}\n\n"
            f"Client's past-period metrics (use only these numbers):\n"
            f"{json.dumps(metrics, indent=2)}\n\nWrite the review now, following all absolute rules.")


def generate_message(first_name, track, metrics, config) -> str:
    if track == "HUMAN_REVIEW":
        raise ValueError("HUMAN_REVIEW must not reach the generator.")

    if config.ANTHROPIC_API_KEY:
        from anthropic import Anthropic
        client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        last_err = None
        for attempt in range(3):
            try:
                resp = client.messages.create(
                    model=config.CLAUDE_MODEL,
                    max_tokens=400,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": _user_msg(first_name, track, metrics)}],
                )
                return "".join(b.text for b in resp.content if b.type == "text").strip()
            except Exception as e:  # transient API errors -> backoff retry
                last_err = e
                log.warning("Claude call failed (attempt %d): %s", attempt + 1, e)
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Claude generation failed after retries: {last_err}")

    return _mock(first_name, track, metrics)


def _mock(first_name, track, m):
    if track == "STANDARD":
        return (f"Hi {first_name}, here's a look back at your recent activity. Over "
                f"{m['num_trades']} trades you finished at {m['total_pl']}, with a "
                f"{m['win_rate_pct']}% win rate and a reward-to-risk ratio of "
                f"{m['reward_risk_ratio']}. You set a stop-loss on {m['sl_set_pct']}% of "
                f"trades — strong risk discipline. A general principle worth remembering: "
                f"consistent stops and a reward-to-risk above 1 tend to make results more "
                f"durable over time. Well done on the habits you've built. "
                f"[MOCK — set ANTHROPIC_API_KEY for real text.]")
    return (f"Hi {first_name}, thanks for reviewing your recent trading. This period was a "
            f"tough one — across {m['num_trades']} trades the result was {m['total_pl']}, "
            f"with a stop-loss set on {m['sl_set_pct']}% of them. The data highlights how a "
            f"consistent stop-loss can limit losing trades: your average loss was "
            f"{m['avg_loss']} versus an average win of {m['avg_win']}. As a general point, "
            f"many traders find defining risk on every position before entering helps keep "
            f"individual losses contained. These numbers describe the past, not a verdict on "
            f"what comes next. [MOCK — set ANTHROPIC_API_KEY for real text.]")
