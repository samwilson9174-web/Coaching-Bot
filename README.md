# Milele Prime — Trade Coaching Bot

A production bot that pulls client trade history from the **MT5 Manager API**,
analyses each client's behaviour, and sends a personalised, motivating,
**educational** review over **Telegram** — with Claude writing the prose inside
hard compliance and tone-safety guardrails. Runs **daily on a schedule** and
**on demand**.

## Design principle
Compliance and tone-safety are **deterministic gates around Claude**, not jobs
trusted to Claude:

```
consent (MT5 opt-in groups) → analysis → tone classifier → Claude (constrained)
   → compliance filter → Telegram delivery → audit log + idempotency state
```

HUMAN_REVIEW clients (severe losses) **never** reach Claude and are **never**
auto-messaged — they're queued for a person.

## Layout
```
coachbot/
  config.py        all settings from env/.env
  logger.py        structured logging
  mt5_source.py    MT5 Manager API adapter (Real + Mock) -> normalised trades
  analysis.py      deterministic per-user metrics (no LLM)
  classifier.py    STANDARD / SOFT / HUMAN_REVIEW / SKIP routing
  generation.py    Claude call, locked-down prompt, per-track template (+mock)
  compliance.py    output filter blocking advice-like text
  telegram.py      Telegram Bot API delivery (dry-run aware)
  store.py         audit log, review queue, sent-state (idempotency)
  pipeline.py      orchestrates one full run
  main.py          CLI / scheduler / health server
requirements.txt
.env.example
Procfile           Railway start command
```

## Two ways to feed the bot data

Set `DATA_SOURCE` in `.env`:

| `DATA_SOURCE` | What it does | When to use |
|---------------|--------------|-------------|
| `file` | Reads a manually-exported **Excel/CSV** trade history from a local folder. No server, no network. | **Way-out two** — test path. Files already on your device. |
| `brokeret` | **Fetches** the daily export from the Brokeret CRM cloud (http/sftp/ftp/s3), lands it locally, then reads it exactly like `file`. | **Way-out one** — production. Brokeret stores the daily file. |
| `mt5`  | Live pull via the MT5 Manager API. | Later, once you have direct MT5 server access. |
| `mock` | Synthetic test data. | Testing the pipeline anywhere. |

### The two way-outs share everything except where the file comes from

Both run the identical gated pipeline — consent → analysis → classifier → Claude
(constrained) → compliance filter → Telegram → audit. `brokeret` is literally
`file` with a download step bolted on the front (`BrokeretDataSource` fetches,
then delegates to `FileDataSource`). So whatever you validate in way-out two is
exactly what runs in way-out one — the only new failure surface is the fetch.

### File workflow (no server) — the easy path

1. In MT5 / Brokeret, export the closed-trade history and your client list as
   `.csv` or `.xlsx`.
2. Drop them in `data/` (or anywhere) and point `TRADES_FILE` / `USERS_FILE` at them.
3. Run `python -m coachbot.main run`.

**Column names are flexible** — the bot maps common aliases case-insensitively
(e.g. `Account`/`Login`, `Instrument`/`Symbol`, `Profit`/`PnL`, `S/L`/`StopLoss`,
`Open Time` with `2026.06.21 09:00:00` or `2026-06-21 09:00:00` formats). If a
column isn't recognised, add its header to the alias lists at the top of
`coachbot/file_source.py`.

**Trade history file** — one row per closed trade. Recognised fields:
`login` and `profit` are required; `symbol, type, volume, open_price,
close_price, sl, tp, sl_hit, tp_hit, open_time, close_time` are used if present.

**Client list file** — one row per client. Required: `login`, `name`.
Optional: `email`, `telegram_id`, `consent`. The **consent column is the gate** —
only rows with consent = 1/true/yes are processed. If your export has no consent
column, the bot assumes the file is already filtered to opted-in clients (so
only export consenting clients).

A client with no `telegram_id` is queued as *unreachable* (logged, not sent) —
the bot never guesses a destination.

## Quick start (mock data, no credentials, nothing sent)
```bash
pip install -r requirements.txt          # python-dotenv; anthropic optional for real text
cp .env.example .env                      # USE_MOCK_DATA=1, SEND_FOR_REAL=0 by default
python -m coachbot.main run               # on-demand run
```
Outputs:
- `output/audit_log.jsonl` — every generated/sent message (regulator-ready)
- `output/human_review_queue.jsonl` — clients a human must handle
- `output/sent_state.json` — prevents double-sending the same day

## Run modes
```bash
python -m coachbot.main run            # run once now
python -m coachbot.main run --force    # run now, ignore "already sent today"
python -m coachbot.main schedule       # daily run at RUN_AT + health endpoint (Railway)
python -m coachbot.main health         # health endpoint only
```

## Going live (each switch is independent)
1. **Real Claude text:** set `ANTHROPIC_API_KEY` (model `claude-haiku-4-5`).
2. **Real data:** set `USE_MOCK_DATA=0` and the `MT5_*` vars. The MT5 Manager
   binding (`MT5Manager` wheel from your MT5 licence) must be installed on the
   host and able to reach your MT5 server. Only `mt5_source.py` touches the
   vendor API — everything else is unchanged.
3. **Consent:** put ONLY opted-in client groups in `MT5_GROUPS`. The bot only
   ever fetches users from those groups. A client with no Telegram handle is
   queued as unreachable, never guessed at.
4. **Real Telegram:** set `TELEGRAM_BOT_TOKEN`. Note: a Telegram bot can only
   message users who have pressed **Start** on the bot first — store each
   client's numeric chat_id (recommended) or @username in their MT5 record.
5. **Actually send:** set `SEND_FOR_REAL=1` (until then everything is dry-run).

## Deploy on Railway
- Start command (Procfile): `python -m coachbot.main schedule`
- Set all env vars in the Railway dashboard.
- `RUN_AT` is UTC `HH:MM`; the daily run fires once per day at that time.
- Health endpoint on `$PORT` (`/health` returns 200) keeps the service alive.
- Persist `output/` on a Railway volume so the audit log and sent-state survive
  restarts (otherwise idempotency resets on redeploy).

## Compliance — required before any real client message
- Sign-off on `generation.py::SYSTEM_PROMPT` and `compliance.py::FORBIDDEN_PATTERNS`.
  These are where the FSA-facing rules live.
- Tune `classifier.py` thresholds (all in `.env`) with compliance.
- Pilot: keep `SEND_FOR_REAL=0`, have a human read every message in the audit
  log for the first cohort, then flip the switch.

## How the MT5 pull works (mt5_source.py)
`RealMT5DataSource` connects with the Manager API, lists logins per group,
fetches **deals**, and pairs entry/exit deals by `PositionID` into normalised
closed-trade records (symbol, direction, volume, SL/TP, SL/TP-hit, times,
profit). SL/TP-hit is inferred from exit price vs SL/TP; if your MT5 build
exposes a deal **Reason** code, prefer that — one clearly-marked spot in the
pairing function. `MockMT5DataSource` returns the same shape so the whole bot
runs anywhere for testing.
```
