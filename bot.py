import os
import requests
import datetime
import json
from anthropic import Anthropic

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

claude = Anthropic(api_key=ANTHROPIC_API_KEY)

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": "Bearer " + SUPABASE_KEY,
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

def sb_insert(table, record):
    r = requests.post(SUPABASE_URL + "/rest/v1/" + table, headers=HEADERS, json=record)
    return r.json()

def sb_select(table, query=""):
    r = requests.get(SUPABASE_URL + "/rest/v1/" + table + query, headers=HEADERS)
    return r.json()

def sb_update(table, match_field, match_value, data):
    r = requests.patch(SUPABASE_URL + "/rest/v1/" + table + "?" + match_field + "=eq." + str(match_value), headers=HEADERS, json=data)
    return r.json()

# ─────────────────────────────────────────
# BRAIN — stores lessons learned from losses
# ─────────────────────────────────────────
def load_brain():
    try:
        result = sb_select("brain", "?order=updated_at.desc&limit=1")
        if isinstance(result, list) and len(result) > 0:
            data = result[0]
            if isinstance(data.get("lessons"), str):
                data["lessons"] = json.loads(data["lessons"])
            if isinstance(data.get("avoid_conditions"), str):
                data["avoid_conditions"] = json.loads(data["avoid_conditions"])
            if isinstance(data.get("trust_conditions"), str):
                data["trust_conditions"] = json.loads(data["trust_conditions"])
            return data
    except:
        pass
    return {"lessons": [], "avoid_conditions": [], "trust_conditions": [], "total_adjustments": 0}

def save_brain(brain):
    try:
        existing = sb_select("brain", "?order=updated_at.desc&limit=1")
        brain["updated_at"] = datetime.datetime.utcnow().isoformat()
        save = {
            "lessons": json.dumps(brain.get("lessons", [])[-20:]),
            "avoid_conditions": json.dumps(brain.get("avoid_conditions", [])[-20:]),
            "trust_conditions": json.dumps(brain.get("trust_conditions", [])[-20:]),
            "total_adjustments": brain.get("total_adjustments", 0),
            "updated_at": brain["updated_at"]
        }
        if isinstance(existing, list) and len(existing) > 0:
            sb_update("brain", "id", existing[0]["id"], save)
        else:
            sb_insert("brain", save)
    except Exception as e:
        print("  Brain save error: " + str(e))

def analyze_loss_and_learn(loss_trade, brain):
    print("  -> Analyzing loss to learn...")
    try:
        rsi = float(loss_trade.get("rsi", 50))
        bb = float(loss_trade.get("bb_position", 50))
        vol = float(loss_trade.get("vol_spike", 1))
        mom = float(loss_trade.get("momentum_1h", 0))
        signal = loss_trade.get("signal", "")

        lesson = "LOSS at " + loss_trade["created_at"][:16] + ": Signal=" + signal
        lesson += " | RSI=" + str(rsi) + " | BB=" + str(bb) + " | Vol=" + str(vol) + " | Mom=" + str(mom)

        avoid = ""
        if signal == "UP" and rsi > 65:
            avoid = "Avoid UP when RSI > 65 (overbought trap)"
        elif signal == "DOWN" and rsi < 35:
            avoid = "Avoid DOWN when RSI < 35 (oversold trap)"
        elif vol < 0.7:
            avoid = "Avoid signals when volume < 0.7x average (low conviction)"
        elif abs(mom) < 0.05:
            avoid = "Avoid signals when momentum near zero (choppy market)"
        elif signal == "UP" and bb > 80:
            avoid = "Avoid UP when price near BB top (overextended)"
        elif signal == "DOWN" and bb < 20:
            avoid = "Avoid DOWN when price near BB bottom (oversold)"
        else:
            avoid = "Review: signal was wrong despite normal conditions - market was unpredictable"

        brain["lessons"].append(lesson)
        brain["avoid_conditions"].append(avoid)
        brain["total_adjustments"] = brain.get("total_adjustments", 0) + 1
        save_brain(brain)
        print("  -> Lesson learned: " + avoid)
        return avoid
    except Exception as e:
        print("  -> Learn error: " + str(e))
        return ""

# ─────────────────────────────────────────
# MARKET DATA
# ─────────────────────────────────────────
def get_candles(interval, limit):
    base = "https://api.binance.com/api/v3"
    raw = requests.get(base + "/klines?symbol=BTCUSDT&interval=" + interval + "&limit=" + str(limit)).json()
    return [{"open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])} for c in raw]

def calc_rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    return round(100 - (100 / (1 + ag / al)), 2) if al > 0 else 50

def calc_ema(data, period):
    k = 2 / (period + 1)
    e = data[0]
    for v in data[1:]:
        e = v * k + e * (1 - k)
    return e

def calc_macd(closes):
    macd = calc_ema(closes, 12) - calc_ema(closes, 26)
    signal = calc_ema([calc_ema(closes[:i+1], 12) - calc_ema(closes[:i+1], 26) for i in range(26, len(closes))], 9)
    return round(macd, 2), round(signal, 2), round(macd - signal, 2)

def calc_bollinger(closes, period=20):
    sma = sum(closes[-period:]) / period
    std = (sum((c - sma)**2 for c in closes[-period:]) / period) ** 0.5
    upper = round(sma + 2 * std, 2)
    lower = round(sma - 2 * std, 2)
    pos = round((closes[-1] - lower) / (upper - lower) * 100, 1) if upper != lower else 50
    return upper, lower, pos

def calc_stochastic(candles, period=14):
    highs = [c["high"] for c in candles[-period:]]
    lows = [c["low"] for c in candles[-period:]]
    close = candles[-1]["close"]
    highest = max(highs)
    lowest = min(lows)
    return round((close - lowest) / (highest - lowest) * 100, 1) if highest != lowest else 50

def calc_atr(candles, period=14):
    trs = []
    for i in range(1, len(candles)):
        hl = candles[i]["high"] - candles[i]["low"]
        hc = abs(candles[i]["high"] - candles[i-1]["close"])
        lc = abs(candles[i]["low"] - candles[i-1]["close"])
        trs.append(max(hl, hc, lc))
    return round(sum(trs[-period:]) / period, 2)

def calc_vwap(candles):
    total_pv = sum(((c["high"] + c["low"] + c["close"]) / 3) * c["volume"] for c in candles)
    total_v = sum(c["volume"] for c in candles)
    return round(total_pv / total_v, 2) if total_v > 0 else 0

def detect_patterns(candles):
    patterns = []
    c = candles
    body = abs(c[-1]["close"] - c[-1]["open"])
    lower_wick = min(c[-1]["open"], c[-1]["close"]) - c[-1]["low"]
    upper_wick = c[-1]["high"] - max(c[-1]["open"], c[-1]["close"])
    if body < (c[-1]["high"] - c[-1]["low"]) * 0.1:
        patterns.append("DOJI")
    if lower_wick > body * 2 and upper_wick < body * 0.5:
        patterns.append("HAMMER(bullish)")
    if upper_wick > body * 2 and lower_wick < body * 0.5:
        patterns.append("SHOOTING_STAR(bearish)")
    if len(c) >= 2:
        prev_body = abs(c[-2]["close"] - c[-2]["open"])
        curr_body = body
        if c[-2]["close"] < c[-2]["open"] and c[-1]["close"] > c[-1]["open"] and curr_body > prev_body:
            patterns.append("BULLISH_ENGULFING")
        if c[-2]["close"] > c[-2]["open"] and c[-1]["close"] < c[-1]["open"] and curr_body > prev_body:
            patterns.append("BEARISH_ENGULFING")
    if len(c) >= 3:
        if all(c[-i]["close"] > c[-i]["open"] for i in range(1, 4)):
            patterns.append("3_GREEN(bullish_momentum)")
        if all(c[-i]["close"] < c[-i]["open"] for i in range(1, 4)):
            patterns.append("3_RED(bearish_momentum)")
    return patterns if patterns else ["NO_PATTERN"]

def analyze_timeframe(interval, limit, label):
    candles = get_candles(interval, limit)
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    rsi = calc_rsi(closes)
    macd, macd_signal, macd_hist = calc_macd(closes)
    bb_upper, bb_lower, bb_pos = calc_bollinger(closes)
    stoch = calc_stochastic(candles)
    atr = calc_atr(candles)
    vwap = calc_vwap(candles)
    avg_vol = sum(volumes[-20:-1]) / 19 if len(volumes) > 20 else sum(volumes) / len(volumes)
    vol_spike = round(volumes[-1] / avg_vol, 2)
    momentum = round(((closes[-1] - closes[-4]) / closes[-4]) * 100, 3) if len(closes) >= 4 else 0
    return {
        "label": label,
        "close": round(closes[-1], 2),
        "rsi": rsi,
        "macd_hist": macd_hist,
        "bb_pos": bb_pos,
        "stoch": stoch,
        "atr": atr,
        "vwap": vwap,
        "price_vs_vwap": "ABOVE" if closes[-1] > vwap else "BELOW",
        "vol_spike": vol_spike,
        "momentum": momentum,
        "trend": "UPTREND" if closes[-1] > sum(closes[-20:]) / 20 else "DOWNTREND",
        "patterns": detect_patterns(candles),
        "last_5_candles": ["G" if c["close"] > c["open"] else "R" for c in candles[-5:]]
    }

def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        d = r.json()["data"][0]
        return {"value": d["value"], "label": d["value_classification"]}
    except:
        return {"value": "N/A", "label": "N/A"}

def get_funding_rate():
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1", timeout=5)
        rate = float(r.json()[0]["fundingRate"]) * 100
        s = "OVERLEVERAGED_LONG" if rate > 0.05 else "OVERLEVERAGED_SHORT" if rate < -0.05 else "NEUTRAL"
        return {"rate": round(rate, 4), "sentiment": s}
    except:
        return {"rate": "N/A", "sentiment": "N/A"}

def get_orderbook():
    try:
        r = requests.get("https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=20", timeout=5)
        d = r.json()
        bid_vol = sum(float(b[1]) for b in d["bids"])
        ask_vol = sum(float(a[1]) for a in d["asks"])
        ratio = round(bid_vol / ask_vol, 2)
        pressure = "BUY_PRESSURE" if ratio > 1.2 else "SELL_PRESSURE" if ratio < 0.8 else "BALANCED"
        return {"ratio": ratio, "pressure": pressure}
    except:
        return {"ratio": "N/A", "pressure": "N/A"}

def get_market_stats():
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT", timeout=5)
        d = r.json()
        return {"change_24h": float(d["priceChangePercent"]), "high_24h": float(d["highPrice"]), "low_24h": float(d["lowPrice"]), "volume_24h_m": round(float(d["quoteVolume"]) / 1e6, 1)}
    except:
        return {}

def get_news():
    try:
        r = requests.get("https://cryptopanic.com/api/v1/posts/?auth_token=public&currencies=BTC&kind=news&public=true", timeout=5)
        posts = r.json().get("results", [])[:5]
        return [p["title"] for p in posts]
    except:
        return ["News unavailable"]

# ─────────────────────────────────────────
# HISTORY
# ─────────────────────────────────────────
def get_history():
    result = sb_select("signals", "?order=created_at.desc&limit=200")
    return result if isinstance(result, list) else []

def calc_stats(history):
    resolved = [t for t in history if t.get("outcome") in ["WIN", "LOSS"]]
    if not resolved:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "streak": 0, "streak_type": "N/A", "last_5": []}
    wins = sum(1 for t in resolved if t["outcome"] == "WIN")
    streak = 0
    for t in resolved:
        if t["outcome"] == resolved[0]["outcome"]:
            streak += 1
        else:
            break
    return {
        "total": len(resolved),
        "wins": wins,
        "losses": len(resolved) - wins,
        "win_rate": round(wins / len(resolved) * 100, 1),
        "streak": streak,
        "streak_type": resolved[0]["outcome"],
        "last_5": [t["outcome"] for t in resolved[:5]]
    }

# ─────────────────────────────────────────
# ASK CLAUDE
# ─────────────────────────────────────────
def ask_claude(tf1m, tf5m, tf15m, tf1h, tf4h, fear_greed, funding, orderbook, market_stats, news, history, stats, brain):
    hist_text = ""
    if history:
        hist_text = "\nRECENT TRADES (last 20):\n"
        for t in history[:20]:
            hist_text += "  " + t["created_at"][:16] + " | " + t["signal"] + " | $" + str(t["btc_price"]) + " | RSI:" + str(t.get("rsi","?")) + " | " + t.get("outcome", "PENDING") + "\n"

    lessons_text = ""
    if brain.get("avoid_conditions"):
        lessons_text = "\nLESSONS LEARNED FROM PAST LOSSES (follow these rules!):\n"
        for lesson in brain["avoid_conditions"][-10:]:
            lessons_text += "  - " + lesson + "\n"
        lessons_text += "Total adjustments made: " + str(brain.get("total_adjustments", 0)) + "\n"

    news_text = "\nLATEST BTC NEWS:\n" + "\n".join("  - " + h for h in news[:5])

    prompt = """You are an elite self-learning BTC quantitative trader. You analyze multiple timeframes, sentiment, and your own past mistakes to make the best possible 15-minute prediction for Kalshi.

CURRENT BTC PRICE: $""" + str(tf15m["close"]) + """

═══ MULTI-TIMEFRAME ANALYSIS ═══

1-MIN: RSI=""" + str(tf1m["rsi"]) + """ Stoch=""" + str(tf1m["stoch"]) + """ MACD_H=""" + str(tf1m["macd_hist"]) + """ BB=""" + str(tf1m["bb_pos"]) + """% Vol=""" + str(tf1m["vol_spike"]) + """x Mom=""" + str(tf1m["momentum"]) + """% Trend=""" + tf1m["trend"] + """ Candles=""" + "".join(tf1m["last_5_candles"]) + """ Patterns=""" + ",".join(tf1m["patterns"]) + """

5-MIN: RSI=""" + str(tf5m["rsi"]) + """ Stoch=""" + str(tf5m["stoch"]) + """ MACD_H=""" + str(tf5m["macd_hist"]) + """ BB=""" + str(tf5m["bb_pos"]) + """% Vol=""" + str(tf5m["vol_spike"]) + """x Mom=""" + str(tf5m["momentum"]) + """% Trend=""" + tf5m["trend"] + """ Candles=""" + "".join(tf5m["last_5_candles"]) + """ Patterns=""" + ",".join(tf5m["patterns"]) + """

15-MIN: RSI=""" + str(tf15m["rsi"]) + """ Stoch=""" + str(tf15m["stoch"]) + """ MACD_H=""" + str(tf15m["macd_hist"]) + """ BB=""" + str(tf15m["bb_pos"]) + """% Vol=""" + str(tf15m["vol_spike"]) + """x Mom=""" + str(tf15m["momentum"]) + """% Trend=""" + tf15m["trend"] + """ Candles=""" + "".join(tf15m["last_5_candles"]) + """ Patterns=""" + ",".join(tf15m["patterns"]) + """

1-HOUR: RSI=""" + str(tf1h["rsi"]) + """ MACD_H=""" + str(tf1h["macd_hist"]) + """ BB=""" + str(tf1h["bb_pos"]) + """% Mom=""" + str(tf1h["momentum"]) + """% Trend=""" + tf1h["trend"] + """

4-HOUR: RSI=""" + str(tf4h["rsi"]) + """ MACD_H=""" + str(tf4h["macd_hist"]) + """% BB=""" + str(tf4h["bb_pos"]) + """% Mom=""" + str(tf4h["momentum"]) + """% Trend=""" + tf4h["trend"] + """

═══ MARKET SENTIMENT ═══
Fear & Greed: """ + str(fear_greed["value"]) + "/100 (" + fear_greed["label"] + """)
Funding Rate: """ + str(funding["rate"]) + """% (""" + funding["sentiment"] + """)
Order Book: """ + orderbook["pressure"] + """ (ratio: """ + str(orderbook["ratio"]) + """)
24h Change: """ + str(market_stats.get("change_24h","N/A")) + """% | High: $""" + str(market_stats.get("high_24h","N/A")) + """ | Low: $""" + str(market_stats.get("low_24h","N/A")) + """
""" + news_text + """

═══ PERFORMANCE ═══
Win Rate: """ + str(stats["win_rate"]) + """% | Total: """ + str(stats["total"]) + """ | W:""" + str(stats["wins"]) + """ L:""" + str(stats["losses"]) + """
Streak: """ + str(stats["streak"]) + """x """ + stats["streak_type"] + """ | Last 5: """ + " ".join(stats["last_5"]) + """
""" + hist_text + """
""" + lessons_text + """

═══ DECISION RULES ═══
- ONLY signal when 1m + 5m + 15m ALL agree on direction
- If ANY timeframe conflicts = WAIT
- Strictly follow all lessons learned from past losses above
- RSI > 75 near BB top = likely reversal DOWN
- RSI < 25 near BB bottom = likely reversal UP
- Low volume (< 0.7x) = unreliable signal = WAIT
- When in doubt = WAIT (protecting capital beats gambling)

Reply with ONLY one word: UP, DOWN, or WAIT"""

    msg = claude.messages.create(
        model="claude-opus-4-6",
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip().upper()
    if "UP" in raw:
        return "UP"
    if "DOWN" in raw:
        return "DOWN"
    return "WAIT"

# ─────────────────────────────────────────
# SAVE SIGNAL
# ─────────────────────────────────────────
def save_signal(signal, tf15m):
    record = {
        "signal": signal,
        "btc_price": tf15m["close"],
        "rsi": tf15m["rsi"],
        "macd": tf15m["macd_hist"],
        "momentum_1h": tf15m["momentum"],
        "vol_spike": tf15m["vol_spike"],
        "bb_position": tf15m["bb_pos"],
        "outcome": "PENDING",
        "created_at": datetime.datetime.utcnow().isoformat()
    }
    result = sb_insert("signals", record)
    if isinstance(result, list) and len(result) > 0:
        return result[0]["id"]
    return "?"

# ─────────────────────────────────────────
# RESOLVE + LEARN FROM LOSS
# ─────────────────────────────────────────
def resolve_and_learn(current_price, brain):
    pending = sb_select("signals", "?outcome=eq.PENDING&order=created_at.desc&limit=1")
    if not pending or not isinstance(pending, list):
        return
    last = pending[0]
    last_price = float(last["btc_price"])
    signal = last["signal"]

    if signal == "WAIT":
        outcome = "SKIPPED"
    elif signal == "UP" and current_price > last_price:
        outcome = "WIN"
    elif signal == "DOWN" and current_price < last_price:
        outcome = "WIN"
    else:
        outcome = "LOSS"

    sb_update("signals", "id", last["id"], {"outcome": outcome, "exit_price": current_price})
    print("  Resolved: " + signal + " -> " + outcome + " ($" + str(last_price) + " -> $" + str(current_price) + ")")

    if outcome == "LOSS":
        analyze_loss_and_learn(last, brain)

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def main():
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    print("==================================================")
    print("  BTC ORACLE ULTRA  " + now + " UTC")
    print("==================================================")

    print("  -> Loading brain (learned rules)...")
    brain = load_brain()
    print("  -> Brain has " + str(len(brain.get("avoid_conditions", []))) + " learned rules from " + str(brain.get("total_adjustments", 0)) + " adjustments")

    print("  -> Analyzing 5 timeframes...")
    tf1m  = analyze_timeframe("1m",  50, "1MIN")
    tf5m  = analyze_timeframe("5m",  50, "5MIN")
    tf15m = analyze_timeframe("15m", 50, "15MIN")
    tf1h  = analyze_timeframe("1h",  50, "1HOUR")
    tf4h  = analyze_timeframe("4h",  50, "4HOUR")
    print("  -> BTC: $" + str(tf15m["close"]) + " | RSI(15m): " + str(tf15m["rsi"]) + " | Trend: " + tf15m["trend"])

    print("  -> Getting sentiment...")
    fear_greed = get_fear_greed()
    funding    = get_funding_rate()
    orderbook  = get_orderbook()
    market_stats = get_market_stats()
    print("  -> F&G: " + str(fear_greed["value"]) + " | Funding: " + str(funding["rate"]) + "% | OB: " + orderbook["pressure"])

    print("  -> Getting news...")
    news = get_news()

    print("  -> Resolving last signal + learning from any loss...")
    resolve_and_learn(tf15m["close"], brain)

    print("  -> Loading history...")
    history = get_history()
    stats = calc_stats(history)
    print("  -> Win rate: " + str(stats["win_rate"]) + "% (" + str(stats["wins"]) + "W/" + str(stats["losses"]) + "L) | Streak: " + str(stats["streak"]) + "x " + stats["streak_type"])

    print("  -> Asking Claude with all data + learned rules...")
    signal = ask_claude(tf1m, tf5m, tf15m, tf1h, tf4h, fear_greed, funding, orderbook, market_stats, news, history, stats, brain)

    print("")
    print("  ==================")
    print("  SIGNAL: " + signal)
    print("  ==================")
    print("")

    sid = save_signal(signal, tf15m)
    print("  -> Saved (id: " + str(sid) + ")")
    print("  Done.")

main()
