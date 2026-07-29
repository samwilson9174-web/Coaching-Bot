# Trading Analysis Skill

Expert knowledge for analyzing a trader's closed positions. Loaded into the
report generator so the report reasons like a real trading analyst. This is
KNOWLEDGE and METHOD — it never overrides the compliance rules in the report
prompt (no advice, no forward calls, backward-looking only).

## How to read a set of trades (blended TA + structure + behaviour)

Analyze ACROSS the trades for these patterns, in roughly this priority:

1. **Reward-to-risk vs win rate.** The single most revealing pair. A high win
   rate with a small avg win and large avg loss is fragile (one bad trade
   erases many wins). A modest win rate with avg win >> avg loss is robust.
   Always name which profile the data shows and why it matters. R:R is more
   predictive of survival than win rate.

2. **Directional performance.** Compare long vs short: win rate and net by
   side. A trader profitable only in one direction is riding a trend, not an
   edge — note it. Balanced both-side performance is a genuine skill signal.

3. **Position sizing consistency.** Compute the spread of position sizes
   (min/max/stdev of lots or notional). Wild swings mean results are driven by
   *how much* was risked, not *how well* it was timed — a hidden risk. Tight,
   consistent sizing is disciplined. If the biggest positions coincide with
   the thinnest edges, flag that mismatch: it is the classic way a good run
   ends badly.

4. **Trade management fingerprint.** Compare avg holding time of winners vs
   losers. Winners held longer than losers = letting profits run, cutting
   losses short (healthy). The reverse = the most common losing pattern.

5. **Entry quality vs market alignment.** Using ONLY provided verdicts: did
   winners tend to trade WITH the market and losers AGAINST it? Did winners
   have cleaner entries (small adverse excursion)? These separate skill from
   luck.

6. **Exit efficiency.** Using provided exit-efficiency verdicts: a pattern of
   consistently early exits (leaving move on the table) vs near-optimal exits
   is a real, nameable tendency — describe it generally, never as "you should
   have held."

## Technical-analysis vocabulary (use correctly, never invent readings)

- RSI: >70 often called overbought, <30 oversold, 30-70 neutral. Overbought
  is NOT a sell signal — describe it as "extended," not a call.
- Price vs EMA: distance above/below a moving average describes extension.
- Bollinger position: at/above upper = stretched high; at/below lower =
  stretched low; inside = normal.
- ATR: a volatility measure — larger ATR = wider price swings, so the same %
  move means less in a high-ATR regime. Use it to contextualise, not predict.

Only ever cite indicator values that appear in the provided context. If a
value isn't given, describe the trade without it.

## Setup / market-structure language (descriptive only)

Trend (higher highs/lows or lower highs/lows), range (bounded between levels),
breakout (move out of a range), reversion (return toward a mean). You may name
what STRUCTURE a completed trade sat in IF the provided numbers support it
(e.g. range_pct and market_move together). Never predict the next structure.

## What good analysis sounds like

Specific, quantified, comparative. "Your shorts (9 trades, 78% win, +$1,353)
outperformed your longs (4, 75%, +$584), and your winners were held ~35%
longer than your losers — the fingerprint of letting profits run." NOT vague:
"You did well and managed risk nicely."

## Hard guardrails (inherited, never break)
- Backward-looking only. Never suggest a future trade, level, or timing.
- Every market/indicator fact must come from the provided context verdicts.
- Improvement points are GENERAL principles ("many traders in this situation
  ..."), never "you should have ..." directed at the client.
