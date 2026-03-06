"""
BTC ORACLE — bot.py
Runs every 15 min via Railway cron
"""

import os
import json
import requests
import datetime
from anthropic import Anthropic

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL      = os.environ["SUPABASE_URL"]
SUPABASE_KEY      = os.environ["SUPABASE_KEY"]

claude = Anthropic(api_key=ANTHROPIC_API_KEY)

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# ─────────────────────────────────────────
# SUPABASE HELPERS (no SDK, just requests)
# ─────────────────────────────────────────
def sb_insert(table, record):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=HEADERS, json=record)
    return r.json()

def sb_select(table, query=""):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}{query}", headers=HEADERS)
    return r.json()

def sb_update(table, match_field, match_value, data):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?{match_field}=eq.{match_value}",
        headers=HEADERS, json=data
    )
    return r.json()

# ─────────────────────────────────────────
# STEP 1: Get BTC data from Binance
# ─────────────────────────────────────────
def get_btc_data():
    base = "https://api.binance.com/api/v3"
    price      = float(requests.get(f"{base}/ticker/price?symbol=BTCUSDT").json()["price"])
    stats      = requests.get(f"{base}/ticker/24hr?symbol=BTCUSDT").json()
    change_24h = float(stats["priceChangePercent"])
    raw        = requests.get(f"{base}/klines?symbol=BTCUSDT&interval=15m&limit=40").json()
    candles    = [{"open": float(c[1]), "high": float(c[2]), "low": float(c[3]),
                   "close": float(c[4]), "volume": float(c[5])} for c in raw]
    closes     = [c["close"] for c in candles]
    volumes    = [c["volume"] for c in candles]

    # RSI
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag  = sum(gains[-14:]) / 14
    al  = sum(losses[-14:]) / 14
    rsi = round(100 - (100 / (1 + ag / al)), 2) if al > 0 else 50

    # MACD
    def ema(data, p):
        k = 2 / (p + 1); e = data[0]
        for v in data[1:]: e = v * k + e * (1 - k)
        return e
    macd = round(ema(closes, 12) - ema(closes, 26), 2)

    # Momentum
    momentum_1h = round(((closes[-1] - closes[-4])  / closes[-4])  * 100, 3)
    momentum_4h = round(((closes[-1] - closes[-16]) / closes[-16]) * 100, 3)

    # Volume spike
    avg_vol   = sum(volumes[-20:-1]) / 19
    vol_spike = round(volumes[-1] / avg_vol, 2)

    # Bollinger Bands
    sma20    = sum(closes[-20:]) / 20
    std20    = (sum((c - sma20)**2 for c in closes[-20:]) / 20) ** 0.5
    bb_upper = round(sma20 + 2 * std20, 2)
    bb_lower = round(sma20 - 2 * std20, 2)
    bb_pos   = round((closes[-1] - bb_lower) / (bb_upper - bb_lower) * 100, 1)

    pattern = ["GREEN" if c["close"] > c["open"] else "RED" for c in candles[-3:]]

    return {
        "price": round(price, 2), "change_24h": change_24h,
        "rsi": rsi, "macd": macd,
        "momentum_1h": momentum_1h, "momentum_4h": momentum_4h,
        "vol_spike": vol_spike, "bb_position": bb_pos,
        "last_3_candles": pattern
    }

# ─────────────────────────────────────────
# STEP 2: Load history
# ─────────────────────────────────────────
def get_history():
    return sb_select("signals", "?order=created_at.desc&limit=100")

def calc_win_rate(history):
    resolved = [t for t in history if t.get("outcome") in ["WIN", "LOSS"]]
    if not resolved: return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0}
    wins = sum(1 for t in resolved if t["outcome"] == "WIN")
    return {"total": len(resolved), "wins": wins,
            "losses": len(resolved) - wins,
            "win_rate": round(wins / len(resolved) * 100, 1)}

# ─────────────────────────────────────────
# STEP 3: Ask Claude
# ─────────────────────────────────────────
def ask_claude(data, history, stats):
    hist_text = ""
    if history:
        hist_text = "\n\nRECENT TRADES:\n"
        for t in history[:20]:
            hist_text += f"  {t['created_at'][:16]} | {t['signal']} | ${t['btc_price']} | {t.get('outcome','PENDING')}\n"

    prompt = f"""You are an expert BTC short-term trader. Predict if Bitcoin will be HIGHER or LOWER in 15 minutes on Kalshi.

MARKET DATA:
- Price: ${data['price']:,}
- 24h Change: {data['change_24h']}%
- RSI: {data['rsi']} {"(OVERBOUGHT)" if data['rsi'] > 70 else "(OVERSOLD)" if data['rsi'] < 30 else "(NEUTRAL)"}
- MACD: {data['macd']}
- Momentum 1h: {data['momentum_1h']}%
- Momentum 4h: {data['momentum_4h']}%
- Volume Spike: {data['vol_spike']}x
- Bollinger Position: {data['bb_position']}%
- Last 3 Candles: {" → ".join(data['last_3_candles'])}

RECORD: {stats['total']} trades | {stats['win_rate']}% win rate | {stats['wins']}W {stats['losses']}L
{hist_text}

Reply with only one word: UP, DOWN, or WAIT"""

    msg = claude.messages.create(
        model="claude-opus-4-6", max_tokens=10,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip().upper()
    if "UP" in raw:   return "UP"
    if "DOWN" in raw: return "DOWN"
    return "WAIT"

# ─────────────────────────────────────────
# STEP 4: Save signal
# ─────────────────────────────────────────
def save_signal(signal, data):
    record = {
        "signal": signal, "btc_price": data["price"],
        "rsi": data["rsi"], "macd": data["macd"],
        "momentum_1h": data["momentum_1h"],
        "vol_spike": data["vol_spike"],
        "bb_position": data["bb_position"],
        "outcome": "PENDING",
        "created_at": datetime.datetime.utcnow().isoformat()
    }
    result = sb_insert("signals", record)
    return result[0]["id"] if isinstance(result, list) else "?"

# ─────────────────────────────────────────
# STEP 5: Resolve last signal
# ─────────────────────────────────────────
def resolve_last(current_price):
    pending = sb_select("signals", "?outcome=eq.PENDING&order=created_at.desc&limit=1")
    if not pending or not isinstance(pending, list): return
    last       = pending[0]
    last_price = float(last["btc_price"])
    signal     = last["signal"]
    if signal == "WAIT":                              outcome = "SKIPPED"
    elif signal == "UP"   and current_price > last_price: outcome = "WIN"
    elif signal == "DOWN" and current_price < last_price: outcome = "WIN"
    else:                                             outcome = "LOSS"
    sb_update("signals", "id", last["id"], {"outcome": outcome, "exit_price": current_price})
    print(f"  ✓ Resolved: {signal} → {outcome} (${last_price} → ${current_price})")

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def main():
    print(f"\n{'='*50}")
    print(f"  BTC ORACLE  {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"{'='*50}")

    print("  → Pulling BTC data...")
    data = get_btc_data()
    print(f"  → ${data['price']:,} | RSI: {data['rsi']} | Mom: {data['momentum_1h']}%")

    print("  → Resolving last signal...")
    resolve_last(data["price"])

    print("  → Loading history...")
    history = get_history()
    stats   = calc_win_rate(history if isinstance(history, list) else [])
    print(f"  → Win rate: {stats['win_rate']}% ({stats['wins']}W/{stats['losses']}L)")

    print("  → Asking Claude...")
    signal = ask_claude(data, history if isinstance(history, list) else [], stats)

    print(f"\n  ╔══════════════════╗")
    print(f"  ║  SIGNAL: {signal:<7}  ║")
    print(f"  ╚══════════════════╝\n")

    sid = save_signal(signal, data)
    print(f"  → Saved (id: {sid})")
    print(f"  ✓ Done.\n")

if __name__ == "__main__":
    main()
