"""
╔══════════════════════════════════════════╗
║         BTC ORACLE — bot.py              ║
║   Runs every 15 min via Railway cron     ║
║   Pulls data → asks Claude → logs result ║
╚══════════════════════════════════════════╝
"""

import os
import json
import requests
import datetime
from anthropic import Anthropic
from supabase import create_client, Client

# ─────────────────────────────────────────────────────
# YOUR 3 API KEYS — set these in Railway environment
# ─────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL      = os.environ["SUPABASE_URL"]
SUPABASE_KEY      = os.environ["SUPABASE_KEY"]

claude   = Anthropic(api_key=ANTHROPIC_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ─────────────────────────────────────────────────────
# STEP 1: Pull live BTC data from Binance (free, no key)
# ─────────────────────────────────────────────────────
def get_btc_data():
    base = "https://api.binance.com/api/v3"

    price   = float(requests.get(f"{base}/ticker/price?symbol=BTCUSDT").json()["price"])
    stats   = requests.get(f"{base}/ticker/24hr?symbol=BTCUSDT").json()
    change_24h  = float(stats["priceChangePercent"])
    volume_24h  = float(stats["quoteVolume"])

    raw = requests.get(f"{base}/klines?symbol=BTCUSDT&interval=15m&limit=40").json()
    candles = [{"open": float(c[1]), "high": float(c[2]), "low": float(c[3]),
                "close": float(c[4]), "volume": float(c[5])} for c in raw]

    closes  = [c["close"]  for c in candles]
    volumes = [c["volume"] for c in candles]

    # RSI-14
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-14:]) / 14
    al = sum(losses[-14:]) / 14
    rsi = round(100 - (100 / (1 + ag / al)), 2) if al > 0 else 50

    # MACD
    def ema(data, period):
        k = 2 / (period + 1)
        e = data[0]
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

    # Last 3 candle colors
    pattern = ["GREEN" if c["close"] > c["open"] else "RED" for c in candles[-3:]]

    return {
        "price": round(price, 2), "change_24h": change_24h,
        "volume_24h": round(volume_24h, 0), "rsi": rsi, "macd": macd,
        "momentum_1h": momentum_1h, "momentum_4h": momentum_4h,
        "vol_spike": vol_spike, "bb_upper": bb_upper, "bb_lower": bb_lower,
        "bb_position": bb_pos, "last_3_candles": pattern,
        "current_candle": {"open": candles[-1]["open"], "high": candles[-1]["high"],
                           "low": candles[-1]["low"], "close": candles[-1]["close"]}
    }


# ─────────────────────────────────────────────────────
# STEP 2: Load trade history from Supabase
# ─────────────────────────────────────────────────────
def get_trade_history():
    result = supabase.table("signals").select("*").order("created_at", desc=True).limit(100).execute()
    return result.data or []

def calc_win_rate(history):
    resolved = [t for t in history if t.get("outcome") in ["WIN", "LOSS"]]
    if not resolved:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0}
    wins = sum(1 for t in resolved if t["outcome"] == "WIN")
    return {"total": len(resolved), "wins": wins,
            "losses": len(resolved) - wins,
            "win_rate": round(wins / len(resolved) * 100, 1)}


# ─────────────────────────────────────────────────────
# STEP 3: Ask Claude for UP or DOWN signal
# ─────────────────────────────────────────────────────
def ask_claude(market_data, history, win_stats):
    history_text = ""
    if history:
        history_text = "\n\nRECENT TRADE HISTORY (last 20):\n"
        for t in history[:20]:
            outcome = t.get("outcome", "PENDING")
            history_text += f"  {t['created_at'][:16]} | Signal: {t['signal']} | Price: ${t['btc_price']} | Outcome: {outcome}\n"

    prompt = f"""You are an expert BTC short-term trader. Predict whether Bitcoin will be HIGHER or LOWER in the next 15 minutes on Kalshi.

CURRENT MARKET DATA:
- BTC Price: ${market_data['price']:,}
- 24h Change: {market_data['change_24h']}%
- RSI (14): {market_data['rsi']} {"(OVERBOUGHT)" if market_data['rsi'] > 70 else "(OVERSOLD)" if market_data['rsi'] < 30 else "(NEUTRAL)"}
- MACD: {market_data['macd']}
- Momentum 1h: {market_data['momentum_1h']}%
- Momentum 4h: {market_data['momentum_4h']}%
- Volume Spike: {market_data['vol_spike']}x average {"(HIGH VOLUME)" if market_data['vol_spike'] > 1.5 else ""}
- Bollinger Position: {market_data['bb_position']}% {"(NEAR TOP)" if market_data['bb_position'] > 80 else "(NEAR BOTTOM)" if market_data['bb_position'] < 20 else ""}
- Last 3 Candles: {" → ".join(market_data['last_3_candles'])}

PERFORMANCE:
- Total Trades: {win_stats['total']} | Win Rate: {win_stats['win_rate']}%
- Wins: {win_stats['wins']} | Losses: {win_stats['losses']}
{history_text}

Reply with only one word: UP, DOWN, or WAIT"""

    message = claude.messages.create(
        model="claude-opus-4-6",
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = message.content[0].text.strip().upper()
    if "UP" in raw:   return "UP"
    if "DOWN" in raw: return "DOWN"
    return "WAIT"


# ─────────────────────────────────────────────────────
# STEP 4: Save signal to Supabase
# ─────────────────────────────────────────────────────
def save_signal(signal, market_data):
    record = {
        "signal": signal, "btc_price": market_data["price"],
        "rsi": market_data["rsi"], "macd": market_data["macd"],
        "momentum_1h": market_data["momentum_1h"],
        "vol_spike": market_data["vol_spike"],
        "bb_position": market_data["bb_position"],
        "outcome": "PENDING",
        "created_at": datetime.datetime.utcnow().isoformat()
    }
    result = supabase.table("signals").insert(record).execute()
    return result.data[0]["id"]


# ─────────────────────────────────────────────────────
# STEP 5: Resolve previous signal outcome
# ─────────────────────────────────────────────────────
def resolve_last_signal(current_price):
    result = supabase.table("signals").select("*").eq("outcome", "PENDING") \
        .order("created_at", desc=True).limit(1).execute()
    if not result.data:
        return
    last       = result.data[0]
    last_price = float(last["btc_price"])
    signal     = last["signal"]

    if signal == "WAIT":            outcome = "SKIPPED"
    elif signal == "UP"   and current_price > last_price: outcome = "WIN"
    elif signal == "DOWN" and current_price < last_price: outcome = "WIN"
    else:                           outcome = "LOSS"

    supabase.table("signals").update({"outcome": outcome, "exit_price": current_price}) \
        .eq("id", last["id"]).execute()
    print(f"  ✓ Resolved: {signal} → {outcome}  (${last_price} → ${current_price})")


# ─────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────
def main():
    print(f"\n{'='*50}")
    print(f"  BTC ORACLE  {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"{'='*50}")

    print("  → Pulling BTC data...")
    data = get_btc_data()
    print(f"  → ${data['price']:,} | RSI: {data['rsi']} | Mom: {data['momentum_1h']}%")

    print("  → Resolving last signal...")
    resolve_last_signal(data["price"])

    print("  → Loading history...")
    history   = get_trade_history()
    win_stats = calc_win_rate(history)
    print(f"  → Win rate: {win_stats['win_rate']}% ({win_stats['wins']}W/{win_stats['losses']}L)")

    print("  → Asking Claude...")
    signal = ask_claude(data, history, win_stats)

    print(f"\n  ╔══════════════════╗")
    print(f"  ║  SIGNAL: {signal:<7}  ║")
    print(f"  ╚══════════════════╝\n")

    signal_id = save_signal(signal, data)
    print(f"  → Saved (id: {signal_id})")
    print(f"  ✓ Done.\n")

if __name__ == "__main__":
    main()
