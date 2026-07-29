"""
Generate dummy data for the coachbot test path (DATA_SOURCE=file).
Six clients, each engineered to land in a specific classifier route so the
pilot exercises every branch. Default thresholds assumed:
  HUMAN_REVIEW_NET_LOSS=-5000, HUMAN_REVIEW_SINGLE_LOSS=-1500,
  SOFT_SL_PCT=50, SOFT_RR=0.5, SOFT_DRAWDOWN=-1000, OVERTRADE_PER_DAY=5.
"""
import csv, random
from datetime import datetime, timedelta

random.seed(42)
START = datetime(2026, 6, 1, 9, 0, 0)
SYMBOLS = ["EURUSD", "GBPUSD", "XAUUSD", "USDJPY", "BTCUSD", "US30"]

trades = []
ticket = 700000

def add(login, day, hour, symbol, direction, lots, profit, sl_set, hold_min):
    """Emit one closed-trade row."""
    global ticket
    ticket += 1
    o = START + timedelta(days=day, hours=hour)
    c = o + timedelta(minutes=hold_min)
    entry = round(random.uniform(1.0, 2000.0), 2)
    sl_price = round(entry * (0.98 if direction == "buy" else 1.02), 2) if sl_set else 0
    tp_price = round(entry * (1.02 if direction == "buy" else 0.98), 2)
    close_price = round(entry * (1.01 if profit > 0 else 0.99), 2)
    trades.append({
        "Login": login, "Ticket": ticket, "Symbol": symbol,
        "Type": direction, "Lots": lots, "Open Price": entry,
        "Close Price": close_price, "S/L": sl_price, "T/P": tp_price,
        "SL Hit": 1 if (profit < 0 and sl_set) else 0,
        "TP Hit": 1 if profit > 0 else 0,
        "Open Time": o.strftime("%Y-%m-%d %H:%M:%S"),
        "Close Time": c.strftime("%Y-%m-%d %H:%M:%S"),
        "Profit": profit,
    })

# --- 60001 Sara — STANDARD: healthy, disciplined, profitable ---------------
for d in range(20):
    win = random.random() < 0.6
    add(60001, d, random.randint(0, 6), random.choice(SYMBOLS),
        random.choice(["buy", "sell"]), round(random.uniform(0.1, 0.5), 2),
        round(random.uniform(60, 220), 2) if win else round(-random.uniform(30, 90), 2),
        sl_set=True, hold_min=random.randint(90, 600))

# --- 60002 Omar — SOFT: net negative + weak stop-loss discipline -----------
for d in range(18):
    win = random.random() < 0.4
    add(60002, d, random.randint(0, 6), random.choice(SYMBOLS),
        random.choice(["buy", "sell"]), round(random.uniform(0.1, 0.6), 2),
        round(random.uniform(40, 120), 2) if win else round(-random.uniform(120, 260), 2),
        sl_set=random.random() < 0.3, hold_min=random.randint(60, 400))

# --- 60003 Lena — SOFT: overtrading (many trades/day), small P/L -----------
for d in range(6):
    for k in range(9):  # 9/day >> OVERTRADE_PER_DAY=5
        win = random.random() < 0.5
        add(60003, d, k, random.choice(SYMBOLS),
            random.choice(["buy", "sell"]), round(random.uniform(0.05, 0.3), 2),
            round(random.uniform(15, 60), 2) if win else round(-random.uniform(15, 70), 2),
            sl_set=True, hold_min=random.randint(25, 120))

# --- 60004 Raj — SOFT but UNREACHABLE (no telegram in client file) ---------
for d in range(14):
    win = random.random() < 0.45
    add(60004, d, random.randint(0, 6), random.choice(SYMBOLS),
        random.choice(["buy", "sell"]), round(random.uniform(0.1, 0.5), 2),
        round(random.uniform(50, 150), 2) if win else round(-random.uniform(80, 200), 2),
        sl_set=random.random() < 0.4, hold_min=random.randint(45, 300))

# --- 60005 Mia — CONSENT OPT-OUT (never processed regardless of trades) ----
for d in range(10):
    add(60005, d, random.randint(0, 6), random.choice(SYMBOLS),
        random.choice(["buy", "sell"]), round(random.uniform(0.1, 0.4), 2),
        round(random.uniform(-60, 80), 2), sl_set=True, hold_min=random.randint(60, 300))

# --- 60006 Bilal — HUMAN_REVIEW: severe losses incl. one huge single loss --
for d in range(12):
    add(60006, d, random.randint(0, 6), random.choice(["XAUUSD", "BTCUSD", "US30"]),
        random.choice(["buy", "sell"]), round(random.uniform(0.5, 2.0), 2),
        round(-random.uniform(300, 700), 2), sl_set=random.random() < 0.3,
        hold_min=random.randint(30, 240))
add(60006, 5, 12, "BTCUSD", "buy", 2.0, -1850.00, sl_set=False, hold_min=90)  # >HUMAN_REVIEW_SINGLE_LOSS

# write trades (messy real-world-ish headers, MT5 dotted-date friendly reader handles it)
with open("data/trades.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
    w.writeheader(); w.writerows(trades)

# --- clients file: login, name, email, telegram, consent, IB ---------------
clients = [
    (60001, "Sara Iqbal",  "sara@example.com",  "@sara_fx",  1, "IB200"),
    (60002, "Omar Farooq", "omar@example.com",  "@omar_t",   1, "IB200"),
    (60003, "Lena Park",   "lena@example.com",  "@lena_p",   1, "IB201"),
    (60004, "Raj Patel",   "raj@example.com",   "",          1, "IB201"),  # no telegram -> unreachable
    (60005, "Mia Wong",    "mia@example.com",   "@mia_w",    0, "IB100"),  # consent=0 -> skipped
    (60006, "Bilal Ahmed", "bilal@example.com", "@bilal_a",  1, "IB200"),  # HUMAN_REVIEW
]
with open("data/clients.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Account", "Client Name", "E-Mail", "Telegram", "Opt-In", "IB"])
    w.writerows(clients)

# --- IB directory (for the commission run) ---------------------------------
ibs = [
    ("IB100", "Tariq Mahmood", "",      "Shogun",  8, "@tariq_m", "tariq@partners.example"),
    ("IB200", "Yusuf Khan",    "IB100", "Daimyo",  6, "@yusuf_ib", "yusuf@partners.example"),
    ("IB201", "Aisha Rahman",  "IB100", "Samurai", 5, "@aisha_ib", "aisha@partners.example"),
]
with open("data/ibs.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["ib_id", "name", "parent_ib_id", "tier", "rate_per_lot", "telegram_id", "email"])
    w.writerows(ibs)

print(f"Wrote {len(trades)} trades across {len(clients)} clients + {len(ibs)} IBs.")
