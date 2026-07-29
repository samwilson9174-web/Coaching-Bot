# Finance & Metrics Skill

Correct definitions, formulas, and sanity-checks for the financial metrics in a
trade report. Loaded into the report generator so the numbers are described
correctly and errors are caught. KNOWLEDGE and METHOD only — never overrides
the report's compliance rules.

## Metric definitions (state these correctly)

- **Win rate** = winning trades / total trades. A description of frequency, not
  profitability. High win rate ≠ profitable.
- **Net P&L** = sum of all trade results (already net of fees if the data is
  net). Say whether it is gross or net if known; don't assume.
- **Average win / average loss** = mean profit of winners / mean loss of
  losers. The RATIO of these matters more than either alone.
- **Reward-to-risk ratio (R:R)** = avg win / |avg loss|. Above 1 means winners
  are bigger than losers on average. This is the backbone metric of survival.
- **Expectancy** = (win rate × avg win) − (loss rate × |avg loss|). The average
  expected result per trade. Positive expectancy is what makes a strategy
  viable over many trades. Prefer citing expectancy when explaining WHY a
  record is or isn't sustainable.
- **Max drawdown** = largest peak-to-trough drop in cumulative equity. A risk
  measure — how deep the worst stretch got. Contextualise against net profit:
  a drawdown near the size of total profit signals a rough ride.
- **Profit factor** = gross profit / gross loss. Above 1 is profitable; 1.5+ is
  solid; below 1 loses money.

## Sanity checks (catch bad data — do this silently, mention only if relevant)

- If win rate is 100% over many leveraged trades, treat it as a WARNING sign,
  not a triumph: it usually means losses are being held unrealised or only
  winners are recorded. Say so, gently and generally.
- If net P&L doesn't roughly reconcile with (wins × avg win) − (losses × avg
  loss), the data may be inconsistent — note uncertainty rather than asserting.
- If a trade's profit is large but its market move is tiny, the position size
  (leverage × notional) is doing the work, not the timing — an important
  distinction to surface.
- Never state a metric to false precision. Round sensibly; these are estimates
  of behaviour, not accounting.

## Leverage & futures literacy

- On leveraged perpetuals, a small % market move becomes a large % move on
  margin (10x turns 1% into ~10% on margin). When explaining a big P&L on a
  small move, attribute it correctly to leverage/size, not to a big market
  move that didn't happen.
- Funding costs accrue on positions held across funding intervals — long holds
  on perpetuals carry this cost. Mention it as context for very long holds IF
  relevant, without inventing a figure.
- Cross vs isolated margin affects liquidation risk — describe only if the data
  indicates it; never assume.

## How to frame numbers for a non-expert client

Translate every metric into plain meaning. Not "your R:R is 3.74" alone, but
"your winners were about 3.7x the size of your losers, which is what lets the
strategy stay profitable even if your win rate falls." Teach the concept
through their own number.

## Hard guardrails (inherited, never break)
- Educational, backward-looking. No advice, no predictions, no future levels.
- Only use numbers present in the provided context/metrics.
- Improvement points stay general principles, never client-directed "should
  have" instructions.
