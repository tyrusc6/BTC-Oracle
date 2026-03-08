import os
import requests
import datetime
import json
from anthropic import Anthropic

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL      = os.environ["SUPABASE_URL"]
SUPABASE_KEY      = os.environ["SUPABASE_KEY"]

claude = Anthropic(api_key=ANTHROPIC_API_KEY)

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": "Bearer " + SUPABASE_KEY,
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# ───────────────────────────────────────────────────
# SUPABASE HELPERS
# ───────────────────────────────────────────────────

def sb_insert(table, record):
    r = requests.post(SUPABASE_URL + "/rest/v1/" + table, headers=HEADERS, json=record)
    return r.json()

def sb_select(table, query=""):
    r = requests.get(SUPABASE_URL + "/rest/v1/" + table + query, headers=HEADERS)
    return r.json()

def sb_update(table, match_field, match_value, data):
    r = requests.patch(
        SUPABASE_URL + "/rest/v1/" + table + "?" + match_field + "=eq." + str(match_value),
        headers=HEADERS, json=data
    )
    return r.json()

# ───────────────────────────────────────────────────
# BRAIN — persistent memory smarter than a human
# ───────────────────────────────────────────────────

def load_brain():
    try:
        result = sb_select("brain", "?order=updated_at.desc&limit=1")
        if isinstance(result, list) and len(result) > 0:
            b = result[0]
            for field in ["rulebook", "win_patterns", "loss_patterns", "market_memory"]:
                if isinstance(b.get(field), str):
                    try:    b[field] = json.loads(b[field])
                    except: b[field] = []
            return b
    except: pass
    return {
        "rulebook": [], "win_patterns": [], "loss_patterns": [],
        "market_memory": [], "strategy_notes": "", "personality": "",
        "total_trades": 0, "total_wins": 0, "total_losses": 0, "total_adjustments": 0
    }

def save_brain(b):
    try:
        existing = sb_select("brain", "?order=updated_at.desc&limit=1")
        payload = {
            "updated_at":        datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "rulebook":          json.dumps(b.get("rulebook", [])[-50:]),
            "win_patterns":      json.dumps(b.get("win_patterns", [])[-50:]),
            "loss_patterns":     json.dumps(b.get("loss_patterns", [])[-50:]),
            "market_memory":     json.dumps(b.get("market_memory", [])[-100:]),
            "strategy_notes":    str(b.get("strategy_notes", ""))[:3000],
            "personality":       str(b.get("personality", ""))[:2000],
            "total_trades":      b.get("total_trades", 0),
            "total_wins":        b.get("total_wins", 0),
            "total_losses":      b.get("total_losses", 0),
            "total_adjustments": b.get("total_adjustments", 0)
        }
        if isinstance(existing, list) and len(existing) > 0:
            sb_update("brain", "id", existing[0]["id"], payload)
        else:
            sb_insert("brain", payload)
    except Exception as e:
        print("  Brain save error: " + str(e))

# ───────────────────────────────────────────────────
# DEEP REFLECT — writes real lessons after every trade
# ───────────────────────────────────────────────────

def deep_reflect(trade, outcome, brain, all_history):
    try:
        signal = trade.get("signal", "")
        entry  = float(trade.get("btc_price")  or 0)
        exit_p = float(trade.get("exit_price") or entry)
        move   = round(exit_p - entry, 2)
        rsi    = trade.get("rsi")          or "?"
        bb     = trade.get("bb_position")  or "?"
        vol    = trade.get("vol_spike")    or "?"
        mom    = trade.get("momentum_1h")  or "?"

        wins   = brain.get("total_wins", 0)
        losses = brain.get("total_losses", 0)
        total  = wins + losses
        wr     = round(wins / total * 100, 1) if total > 0 else 0

        recent = [t for t in all_history[:20] if t.get("outcome") in ["WIN", "LOSS"]]
        recent_txt = "\n".join([
            t["signal"] + " | RSI:" + str(t.get("rsi","?")) +
            " | BB:" + str(t.get("bb_position","?")) +
            " | Mom:" + str(t.get("momentum_1h","?")) +
            " | Vol:" + str(t.get("vol_spike","?")) +
            " | " + t.get("outcome","?")
            for t in recent
        ]) or "No history yet"

        current_rules    = "\n".join(brain.get("rulebook", [])) or "None yet"
        current_strategy = brain.get("strategy_notes", "")      or "None yet"
        current_persona  = brain.get("personality", "")         or "None yet"

        win_or_loss_field = (
            '"Describe exactly what market conditions made this trade a winner. Be specific about RSI, BB, trend, momentum so it can be replicated."'
            if outcome == "WIN" else '"null"'
        )
        loss_field = (
            '"Describe exactly why this trade failed and what warning signs to watch for. Be specific and actionable."'
            if outcome == "LOSS" else '"null"'
        )

        prompt = (
            "You are the brain of an elite BTC trading bot. A trade just completed.\n"
            "Reflect deeply and write REAL actionable insights — NOT raw numbers.\n\n"
            "TRADE COMPLETED:\n"
            "Signal: " + signal + " | Outcome: " + outcome + "\n"
            "Entry: $" + str(entry) + " | Exit: $" + str(exit_p) + " | Move: $" + str(move) + "\n"
            "RSI: " + str(rsi) + " | BB: " + str(bb) + "% | Volume: " + str(vol) + "x | Momentum: " + str(mom) + "%\n\n"
            "RECORD: " + str(wins) + "W / " + str(losses) + "L (" + str(wr) + "% win rate)\n\n"
            "RECENT TRADES:\n" + recent_txt + "\n\n"
            "YOUR RULEBOOK:\n" + current_rules + "\n\n"
            "YOUR STRATEGY:\n" + current_strategy + "\n\n"
            "YOUR PERSONALITY:\n" + current_persona + "\n\n"
            "Respond in EXACT JSON (no markdown, no backticks):\n"
            "{\n"
            '  "new_rule": "A specific actionable rule from this trade. E.g.: When RSI>70 + BB>80% + negative momentum = strong DOWN. When 4h trend DOWN + 1h RSI<45 = avoid UP. Be precise.",\n'
            '  "win_pattern": ' + win_or_loss_field + ',\n'
            '  "loss_pattern": ' + loss_field + ',\n'
            '  "memory_note": "One market insight to remember forever from this trade (max 20 words).",\n'
            '  "strategy_update": "Rewrite the full trading strategy in 3-5 sentences based on everything learned. Focus on what actually wins.",\n'
            '  "personality_update": "2-3 sentences on trading style: risk tolerance, best setups, what to avoid."\n'
            "}"
        )

        msg = claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw  = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)

        if data.get("new_rule"):
            brain["rulebook"].append(data["new_rule"])
        if data.get("win_pattern") and data["win_pattern"] != "null":
            brain["win_patterns"].append(data["win_pattern"])
        if data.get("loss_pattern") and data["loss_pattern"] != "null":
            brain["loss_patterns"].append(data["loss_pattern"])
        if data.get("memory_note"):
            brain["market_memory"].append(data["memory_note"])
        if data.get("strategy_update"):
            brain["strategy_notes"] = data["strategy_update"]
        if data.get("personality_update"):
            brain["personality"] = data["personality_update"]

        brain["total_adjustments"] = brain.get("total_adjustments", 0) + 1
        print("  -> Rule learned: " + str(data.get("new_rule", "")))
        print("  -> Strategy updated")

    except Exception as e:
        print("  -> Reflect error: " + str(e))
        # Fallback — still writes real language, not raw numbers
        signal = trade.get("signal", "")
        rsi    = float(trade.get("rsi")         or 50)
        bb     = float(trade.get("bb_position") or 50)
        vol    = float(trade.get("vol_spike")   or 1)
        mom    = float(trade.get("momentum_1h") or 0)
        if outcome == "LOSS":
            if signal == "UP" and rsi > 65:
                brain["rulebook"].append("Avoid UP when RSI above 65 — overbought, reversal likely")
            elif signal == "DOWN" and rsi < 35:
                brain["rulebook"].append("Avoid DOWN when RSI below 35 — oversold, bounce likely")
            elif vol < 0.5:
                brain["rulebook"].append("Volume below 0.5x means weak conviction — skip the trade")
            elif signal == "UP" and bb > 80:
                brain["rulebook"].append("Price near top of Bollinger Band (>80%) with UP = bad risk, likely to reverse")
            elif signal == "DOWN" and bb < 20:
                brain["rulebook"].append("Price near bottom of Bollinger Band (<20%) with DOWN = bad risk, likely to bounce")
            else:
                brain["rulebook"].append("Require stronger confluence across timeframes before trading")
            brain["loss_patterns"].append(
                signal + " failed — RSI " + str(rsi) + ", BB " + str(bb) + "%, mom " + str(mom) + "% — avoid these conditions"
            )
        else:
            if signal == "DOWN" and rsi > 60 and bb > 65:
                brain["win_patterns"].append("DOWN wins when RSI>60 and price in upper BB — overbought reversal is reliable setup")
            elif signal == "UP" and rsi < 40 and bb < 35:
                brain["win_patterns"].append("UP wins when RSI<40 and price in lower BB — oversold bounce is reliable setup")
            else:
                brain["win_patterns"].append(
                    signal + " won with RSI " + str(rsi) + ", BB " + str(bb) + "% — strong directional bias present"
                )
        brain["total_adjustments"] = brain.get("total_adjustments", 0) + 1

# ───────────────────────────────────────────────────
# COMMANDS — owner talks to bot
# ───────────────────────────────────────────────────

def process_commands(brain):
    try:
        pending = sb_select("commands", "?status=eq.PENDING&order=created_at.asc&limit=5")
        if not isinstance(pending, list) or len(pending) == 0:
            return
        for cmd in pending:
            text = cmd.get("command", "")
            print("  -> Command received: " + text)

            rulebook_txt    = "\n".join(brain.get("rulebook", [])[-20:]) or "None yet"
            strategy_txt    = brain.get("strategy_notes", "") or "None yet"

            prompt = (
                "You are the brain of a BTC trading bot. Your owner just sent you this command:\n\n"
                "\"" + text + "\"\n\n"
                "Your current rulebook:\n" + rulebook_txt + "\n\n"
                "Your current strategy:\n" + strategy_txt + "\n\n"
                "Respond in EXACT JSON (no markdown, no backticks):\n"
                "{\n"
                '  "response": "Acknowledge the command in 1-2 sentences. Be specific about what you will do differently.",\n'
                '  "rule_to_add": "If the command implies a new trading rule, write it precisely. Else write null.",\n'
                '  "rule_to_remove_keyword": "If the command cancels an existing rule, write a keyword to identify and remove it. Else write null.",\n'
                '  "strategy_update": "If the command changes overall strategy, rewrite it fully here. Else write null."\n'
                "}"
            )

            msg = claude.messages.create(
                model="claude-opus-4-6",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
            try:
                data = json.loads(raw)
                if data.get("rule_to_add") and data["rule_to_add"] != "null":
                    brain["rulebook"].append("[OWNER] " + data["rule_to_add"])
                if data.get("rule_to_remove_keyword") and data["rule_to_remove_keyword"] != "null":
                    kw = data["rule_to_remove_keyword"].lower()
                    brain["rulebook"] = [r for r in brain["rulebook"] if kw not in r.lower()]
                if data.get("strategy_update") and data["strategy_update"] != "null":
                    brain["strategy_notes"] = data["strategy_update"]
                response_text = data.get("response", "Command received.")
            except:
                response_text = "Command received and noted."

            sb_update("commands", "id", cmd["id"], {"status": "DONE", "response": response_text})
            print("  -> Bot replied: " + response_text)
    except Exception as e:
        print("  -> Command error: " + str(e))

# ───────────────────────────────────────────────────
# MARKET DATA
# ───────────────────────────────────────────────────

def get_candles(interval, limit):
    url = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=" + interval + "&limit=" + str(limit)
    raw = requests.get(url, timeout=10).json()
    return [{"open": float(c[1]), "high": float(c[2]), "low": float(c[3]),
              "close": float(c[4]), "volume": float(c[5])} for c in raw]

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
    for v in data[1:]: e = v * k + e * (1 - k)
    return e

def calc_macd(closes):
    macd   = calc_ema(closes, 12) - calc_ema(closes, 26)
    signal = calc_ema([calc_ema(closes[:i+1], 12) - calc_ema(closes[:i+1], 26) for i in range(26, len(closes))], 9)
    return round(macd, 2), round(signal, 2), round(macd - signal, 2)

def calc_bollinger(closes, period=20):
    sma   = sum(closes[-period:]) / period
    std   = (sum((c - sma)**2 for c in closes[-period:]) / period) ** 0.5
    upper = round(sma + 2*std, 2)
    lower = round(sma - 2*std, 2)
    pos   = round((closes[-1] - lower) / (upper - lower) * 100, 1) if upper != lower else 50
    return upper, lower, pos

def calc_stochastic(candles, period=14):
    highs   = [c["high"] for c in candles[-period:]]
    lows    = [c["low"]  for c in candles[-period:]]
    close   = candles[-1]["close"]
    highest, lowest = max(highs), min(lows)
    return round((close - lowest) / (highest - lowest) * 100, 1) if highest != lowest else 50

def calc_atr(candles, period=14):
    trs = []
    for i in range(1, len(candles)):
        trs.append(max(
            candles[i]["high"] - candles[i]["low"],
            abs(candles[i]["high"] - candles[i-1]["close"]),
            abs(candles[i]["low"]  - candles[i-1]["close"])
        ))
    return round(sum(trs[-period:]) / period, 2)

def calc_vwap(candles):
    pv = sum(((c["high"] + c["low"] + c["close"]) / 3) * c["volume"] for c in candles)
    v  = sum(c["volume"] for c in candles)
    return round(pv / v, 2) if v > 0 else 0

def detect_patterns(candles):
    patterns = []
    c    = candles
    body = abs(c[-1]["close"] - c[-1]["open"])
    lw   = min(c[-1]["open"], c[-1]["close"]) - c[-1]["low"]
    uw   = c[-1]["high"] - max(c[-1]["open"], c[-1]["close"])
    rng  = c[-1]["high"] - c[-1]["low"]
    if rng > 0 and body < rng * 0.1:              patterns.append("DOJI")
    if lw > body*2 and uw < body*0.5:             patterns.append("HAMMER(bullish)")
    if uw > body*2 and lw < body*0.5:             patterns.append("SHOOTING_STAR(bearish)")
    if len(c) >= 2:
        pb = abs(c[-2]["close"] - c[-2]["open"])
        if c[-2]["close"] < c[-2]["open"] and c[-1]["close"] > c[-1]["open"] and body > pb:
            patterns.append("BULL_ENGULF")
        if c[-2]["close"] > c[-2]["open"] and c[-1]["close"] < c[-1]["open"] and body > pb:
            patterns.append("BEAR_ENGULF")
    if len(c) >= 3:
        if all(c[-i]["close"] > c[-i]["open"] for i in range(1, 4)): patterns.append("3_GREEN")
        if all(c[-i]["close"] < c[-i]["open"] for i in range(1, 4)): patterns.append("3_RED")
    return patterns if patterns else ["NONE"]

def analyze_tf(interval, limit, label):
    candles   = get_candles(interval, limit)
    closes    = [c["close"]  for c in candles]
    volumes   = [c["volume"] for c in candles]
    rsi       = calc_rsi(closes)
    macd, ms, mh = calc_macd(closes)
    bbu, bbl, bbp = calc_bollinger(closes)
    stoch     = calc_stochastic(candles)
    atr       = calc_atr(candles)
    vwap      = calc_vwap(candles)
    avg_v     = sum(volumes[-20:-1]) / 19 if len(volumes) > 20 else sum(volumes) / len(volumes)
    vol_spike = round(volumes[-1] / avg_v, 2)
    mom       = round(((closes[-1] - closes[-4]) / closes[-4]) * 100, 3) if len(closes) >= 4 else 0
    sma20     = sum(closes[-20:]) / 20
    return {
        "label": label, "close": round(closes[-1], 2),
        "rsi": rsi, "macd_hist": mh, "bb_pos": bbp,
        "stoch": stoch, "atr": atr, "vwap": vwap,
        "price_vs_vwap": "ABOVE" if closes[-1] > vwap else "BELOW",
        "vol_spike": vol_spike, "momentum": mom,
        "trend": "UP" if closes[-1] > sma20 else "DOWN",
        "patterns": detect_patterns(candles),
        "candles": "".join(["G" if c["close"] > c["open"] else "R" for c in candles[-5:]])
    }

def get_fear_greed():
    try:
        r    = requests.get("https://api.alternative.me/fng/?limit=3", timeout=5)
        data = r.json()["data"]
        return [{"value": d["value"], "label": d["value_classification"]} for d in data]
    except: return [{"value": "N/A", "label": "N/A"}]

def get_funding():
    try:
        r    = requests.get("https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1", timeout=5)
        rate = float(r.json()[0]["fundingRate"]) * 100
        s    = "OVER_LONG" if rate > 0.05 else "OVER_SHORT" if rate < -0.05 else "NEUTRAL"
        return {"rate": round(rate, 4), "sentiment": s}
    except: return {"rate": "N/A", "sentiment": "N/A"}

def get_orderbook():
    try:
        r   = requests.get("https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=20", timeout=5)
        d   = r.json()
        bv  = sum(float(b[1]) for b in d["bids"])
        av  = sum(float(a[1]) for a in d["asks"])
        ratio = round(bv / av, 2)
        return {"ratio": ratio, "pressure": "BUY" if ratio > 1.2 else "SELL" if ratio < 0.8 else "BALANCED"}
    except: return {"ratio": "N/A", "pressure": "N/A"}

def get_market_stats():
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT", timeout=5)
        d = r.json()
        return {
            "change_24h": float(d["priceChangePercent"]),
            "high_24h":   float(d["highPrice"]),
            "low_24h":    float(d["lowPrice"]),
            "vol_24h_m":  round(float(d["quoteVolume"]) / 1e6, 1)
        }
    except: return {}

def get_news():
    try:
        r     = requests.get("https://cryptopanic.com/api/v1/posts/?auth_token=public&currencies=BTC&kind=news&public=true", timeout=5)
        posts = r.json().get("results", [])[:6]
        return [p["title"] for p in posts]
    except: return ["News unavailable"]

# ───────────────────────────────────────────────────
# HISTORY & STATS
# ───────────────────────────────────────────────────

def get_history():
    r = sb_select("signals", "?order=created_at.desc&limit=200")
    return r if isinstance(r, list) else []

def calc_stats(history):
    resolved = [t for t in history if t.get("outcome") in ["WIN", "LOSS"]]
    if not resolved:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "streak": 0, "streak_type": "N/A", "last_10": []}
    wins   = sum(1 for t in resolved if t["outcome"] == "WIN")
    streak = 0
    for t in resolved:
        if t["outcome"] == resolved[0]["outcome"]: streak += 1
        else: break
    return {
        "total":      len(resolved),
        "wins":       wins,
        "losses":     len(resolved) - wins,
        "win_rate":   round(wins / len(resolved) * 100, 1),
        "streak":     streak,
        "streak_type": resolved[0]["outcome"],
        "last_10":    [t["outcome"] for t in resolved[:10]]
    }

# ───────────────────────────────────────────────────
# RESOLVE — score the last signal + trigger learning
# ───────────────────────────────────────────────────

def resolve_and_learn(current_price, brain, all_history):
    pending = sb_select("signals", "?outcome=eq.PENDING&order=created_at.desc&limit=1")
    if not pending or not isinstance(pending, list) or len(pending) == 0:
        return
    last       = pending[0]
    last_price = float(last["btc_price"])
    signal     = last["signal"]

    if   signal == "WAIT":                          outcome = "SKIPPED"
    elif signal == "UP"   and current_price > last_price: outcome = "WIN"
    elif signal == "DOWN" and current_price < last_price: outcome = "WIN"
    else:                                           outcome = "LOSS"

    sb_update("signals", "id", last["id"], {"outcome": outcome, "exit_price": current_price})
    print("  Resolved: " + signal + " -> " + outcome +
          " ($" + str(last_price) + " -> $" + str(current_price) + ")")

    if outcome in ["WIN", "LOSS"]:
        brain["total_trades"] = brain.get("total_trades", 0) + 1
        if outcome == "WIN":
            brain["total_wins"]   = brain.get("total_wins", 0)   + 1
        else:
            brain["total_losses"] = brain.get("total_losses", 0) + 1
        print("  -> Reflecting and updating memory...")
        deep_reflect(last, outcome, brain, all_history)

# ───────────────────────────────────────────────────
# ASK CLAUDE — full memory context decision
# ───────────────────────────────────────────────────

def ask_claude(tf1m, tf5m, tf15m, tf1h, tf4h, fg, funding, ob, mstats, news, history, stats, brain):

    rulebook_txt     = "\n".join(["  " + str(i+1) + ". " + r for i, r in enumerate(brain.get("rulebook",     [])[-20:])]) or "  None yet"
    win_patterns_txt = "\n".join(["  + " + p for p in brain.get("win_patterns",  [])[-10:]]) or "  None yet"
    loss_patterns_txt= "\n".join(["  - " + p for p in brain.get("loss_patterns", [])[-10:]]) or "  None yet"
    memory_txt       = "\n".join(["  * " + m for m in brain.get("market_memory", [])[-10:]]) or "  None yet"
    strategy         = brain.get("strategy_notes", "") or "Still developing strategy."
    personality      = brain.get("personality",    "") or "Still developing style."

    resolved = [t for t in history if t.get("outcome") in ["WIN", "LOSS"]][:30]
    hist_txt = "\n".join([
        "  " + t["created_at"][:16] + " | " + t["signal"] +
        " | RSI:" + str(t.get("rsi","?")) +
        " | BB:" + str(t.get("bb_position","?")) +
        " | Mom:" + str(t.get("momentum_1h","?")) +
        " | Vol:" + str(t.get("vol_spike","?")) +
        " | " + t["outcome"]
        for t in resolved
    ]) or "  No resolved trades yet"

    fg_trend  = " -> ".join([str(f["value"]) + "(" + f["label"] + ")" for f in fg[:3]])
    news_txt  = "\n".join(["  - " + h for h in news[:6]])
    last_10   = " ".join(stats["last_10"]) or "None yet"

    prompt = (
        "You are an elite self-learning BTC trading AI. Your memory grows smarter with every trade.\n"
        "Goal: predict if BTC will be HIGHER or LOWER in exactly 15 minutes on Kalshi.\n\n"
        "════ YOUR MEMORY ════\n\n"
        "STRATEGY:\n" + strategy + "\n\n"
        "TRADING PERSONALITY:\n" + personality + "\n\n"
        "RULEBOOK (your own hard-earned rules):\n" + rulebook_txt + "\n\n"
        "WIN PATTERNS (what made you money):\n" + win_patterns_txt + "\n\n"
        "LOSS PATTERNS (what cost you money):\n" + loss_patterns_txt + "\n\n"
        "MARKET MEMORY:\n" + memory_txt + "\n\n"
        "════ LIVE MARKET DATA ════\n\n"
        "BTC PRICE: $" + str(tf15m["close"]) + "\n\n"
        "TIMEFRAMES:\n"
        "1m:  RSI=" + str(tf1m["rsi"])  + " Stoch=" + str(tf1m["stoch"])  + " MACD=" + str(tf1m["macd_hist"])  + " BB=" + str(tf1m["bb_pos"])  + "% Vol=" + str(tf1m["vol_spike"])  + "x Mom=" + str(tf1m["momentum"])  + "% Trend=" + tf1m["trend"]  + " Candles=" + tf1m["candles"]  + " Pat=" + ",".join(tf1m["patterns"])  + "\n"
        "5m:  RSI=" + str(tf5m["rsi"])  + " Stoch=" + str(tf5m["stoch"])  + " MACD=" + str(tf5m["macd_hist"])  + " BB=" + str(tf5m["bb_pos"])  + "% Vol=" + str(tf5m["vol_spike"])  + "x Mom=" + str(tf5m["momentum"])  + "% Trend=" + tf5m["trend"]  + " Candles=" + tf5m["candles"]  + " Pat=" + ",".join(tf5m["patterns"])  + "\n"
        "15m: RSI=" + str(tf15m["rsi"]) + " Stoch=" + str(tf15m["stoch"]) + " MACD=" + str(tf15m["macd_hist"]) + " BB=" + str(tf15m["bb_pos"]) + "% Vol=" + str(tf15m["vol_spike"]) + "x Mom=" + str(tf15m["momentum"]) + "% Trend=" + tf15m["trend"] + " Candles=" + tf15m["candles"] + " Pat=" + ",".join(tf15m["patterns"]) + "\n"
        "1h:  RSI=" + str(tf1h["rsi"])  + " MACD=" + str(tf1h["macd_hist"])  + " BB=" + str(tf1h["bb_pos"])  + "% Mom=" + str(tf1h["momentum"])  + "% Trend=" + tf1h["trend"]  + " Candles=" + tf1h["candles"]  + "\n"
        "4h:  RSI=" + str(tf4h["rsi"])  + " MACD=" + str(tf4h["macd_hist"])  + " BB=" + str(tf4h["bb_pos"])  + "% Mom=" + str(tf4h["momentum"])  + "% Trend=" + tf4h["trend"]  + " Candles=" + tf4h["candles"]  + "\n\n"
        "SENTIMENT:\n"
        "Fear & Greed (3-day): " + fg_trend + "\n"
        "Funding Rate: " + str(funding["rate"]) + "% (" + funding["sentiment"] + ")\n"
        "Order Book: " + ob["pressure"] + " (ratio: " + str(ob["ratio"]) + ")\n"
        "24h Change: " + str(mstats.get("change_24h","N/A")) + "% | High: $" + str(mstats.get("high_24h","N/A")) + " | Low: $" + str(mstats.get("low_24h","N/A")) + "\n\n"
        "NEWS:\n" + news_txt + "\n\n"
        "════ PERFORMANCE ════\n"
        "Win Rate: " + str(stats["win_rate"]) + "% | " + str(stats["wins"]) + "W / " + str(stats["losses"]) + "L | "
        "Streak: " + str(stats["streak"]) + "x " + stats["streak_type"] + "\n"
        "Last 10: " + last_10 + "\n\n"
        "RECENT TRADES:\n" + hist_txt + "\n\n"
        "════ INSTRUCTIONS ════\n"
        "1. Apply your rulebook strictly — you wrote those rules from hard experience\n"
        "2. Check all 5 timeframes — what is the dominant direction?\n"
        "3. Consider sentiment — is the market overleveraged?\n"
        "4. Only WAIT if you genuinely cannot determine direction\n"
        "5. You are accountable to your own rules\n\n"
        "Reply with ONLY one word: UP, DOWN, or WAIT"
    )

    msg = claude.messages.create(
        model="claude-opus-4-6",
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip().upper()
    if "UP"   in raw: return "UP"
    if "DOWN" in raw: return "DOWN"
    return "WAIT"

# ───────────────────────────────────────────────────
# SAVE SIGNAL
# ───────────────────────────────────────────────────

def save_signal(signal, tf15m):
    record = {
        "signal":       signal,
        "btc_price":    tf15m["close"],
        "rsi":          tf15m["rsi"],
        "macd":         tf15m["macd_hist"],
        "momentum_1h":  tf15m["momentum"],
        "vol_spike":    tf15m["vol_spike"],
        "bb_position":  tf15m["bb_pos"],
        "outcome":      "PENDING",
        "created_at":   datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    result = sb_insert("signals", record)
    if isinstance(result, list) and len(result) > 0:
        return result[0]["id"]
    return "?"

# ───────────────────────────────────────────────────
# KALSHI WINDOW CHECK
# ───────────────────────────────────────────────────

def is_kalshi_window():
    """Only place signals at :00, :15, :30, :45 to align with Kalshi 15-min markets"""
    return datetime.datetime.now(datetime.timezone.utc).minute % 15 == 0

# ───────────────────────────────────────────────────
# MAIN
# ───────────────────────────────────────────────────

def main():
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
    print("══════════════════════════════════════════")
    print("  BTC ORACLE  " + now + " UTC")
    print("══════════════════════════════════════════")

    # 1. Load brain
    print("  -> Loading brain...")
    brain  = load_brain()
    wins   = brain.get("total_wins", 0)
    losses = brain.get("total_losses", 0)
    total  = wins + losses
    wr     = round(wins / total * 100, 1) if total > 0 else 0
    print("  -> " + str(len(brain.get("rulebook",[]))) + " rules | " +
          str(brain.get("total_adjustments",0)) + " reflections | " +
          str(wr) + "% lifetime win rate")

    # 2. Process owner commands
    print("  -> Processing commands...")
    process_commands(brain)

    # 3. Analyze market
    print("  -> Analyzing 5 timeframes...")
    tf1m  = analyze_tf("1m",  60, "1M")
    tf5m  = analyze_tf("5m",  60, "5M")
    tf15m = analyze_tf("15m", 60, "15M")
    tf1h  = analyze_tf("1h",  60, "1H")
    tf4h  = analyze_tf("4h",  60, "4H")
    print("  -> $" + str(tf15m["close"]) + " | RSI:" + str(tf15m["rsi"]) +
          " | Trend:" + tf15m["trend"] + " | BB:" + str(tf15m["bb_pos"]) + "%")

    # 4. Get sentiment
    print("  -> Getting sentiment...")
    fg      = get_fear_greed()
    funding = get_funding()
    ob      = get_orderbook()
    mstats  = get_market_stats()
    news    = get_news()
    print("  -> F&G:" + str(fg[0]["value"]) + " | Funding:" + str(funding["rate"]) + "% | OB:" + ob["pressure"])

    # 5. Load history + resolve last trade
    print("  -> Loading history...")
    history = get_history()
    stats   = calc_stats(history)

    print("  -> Resolving last signal + learning...")
    resolve_and_learn(tf15m["close"], brain, history)

    # Refresh after resolution
    history = get_history()
    stats   = calc_stats(history)
    print("  -> Win rate: " + str(stats["win_rate"]) + "% (" +
          str(stats["wins"]) + "W/" + str(stats["losses"]) + "L)")

    # 6. Signal — only on Kalshi 15-min windows
    if is_kalshi_window():
        print("  -> Kalshi window! Asking Claude...")
        signal = ask_claude(tf1m, tf5m, tf15m, tf1h, tf4h, fg, funding, ob, mstats, news, history, stats, brain)
        print("")
        print("  ══════════════════════")
        print("  SIGNAL:  " + signal)
        print("  ══════════════════════")
        print("")
        sid = save_signal(signal, tf15m)
        print("  -> Saved (id: " + str(sid) + ")")
    else:
        mins_until = 15 - (datetime.datetime.now(datetime.timezone.utc).minute % 15)
        print("  -> Monitoring only — next Kalshi window in " + str(mins_until) + " min")

    # 7. Save brain
    save_brain(brain)
    print("  Done.")

main()
