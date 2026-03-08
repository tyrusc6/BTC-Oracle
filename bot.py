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

# ─────────────────────────────────────────────
# SUPABASE
# ─────────────────────────────────────────────

def sb_insert(table, record):
    r = requests.post(SUPABASE_URL + "/rest/v1/" + table, headers=HEADERS, json=record)
    return r.json()

def sb_select(table, query=""):
    r = requests.get(SUPABASE_URL + "/rest/v1/" + table + query, headers=HEADERS)
    return r.json()

def sb_update(table, field, value, data):
    r = requests.patch(
        SUPABASE_URL + "/rest/v1/" + table + "?" + field + "=eq." + str(value),
        headers=HEADERS, json=data
    )
    return r.json()

def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)

# ─────────────────────────────────────────────
# BRAIN
# ─────────────────────────────────────────────

def load_brain():
    try:
        result = sb_select("brain", "?order=updated_at.desc&limit=1")
        if isinstance(result, list) and len(result) > 0:
            b = result[0]
            for f in ["rulebook", "win_patterns", "loss_patterns", "market_memory"]:
                if isinstance(b.get(f), str):
                    try:    b[f] = json.loads(b[f])
                    except: b[f] = []
            return b
    except: pass
    return {
        "rulebook": [], "win_patterns": [], "loss_patterns": [],
        "market_memory": [], "strategy_notes": "", "personality": "",
        "total_trades": 0, "total_wins": 0, "total_losses": 0, "total_adjustments": 0
    }

def save_brain(b):
    try:
        payload = {
            "updated_at":        utcnow().isoformat(),
            "rulebook":          json.dumps(b.get("rulebook",       [])[-50:]),
            "win_patterns":      json.dumps(b.get("win_patterns",   [])[-50:]),
            "loss_patterns":     json.dumps(b.get("loss_patterns",  [])[-50:]),
            "market_memory":     json.dumps(b.get("market_memory",  [])[-100:]),
            "strategy_notes":    str(b.get("strategy_notes", ""))[:3000],
            "personality":       str(b.get("personality",    ""))[:2000],
            "total_trades":      b.get("total_trades",      0),
            "total_wins":        b.get("total_wins",        0),
            "total_losses":      b.get("total_losses",      0),
            "total_adjustments": b.get("total_adjustments", 0)
        }
        brain_id = b.get("id")
        if brain_id:
            # Update the specific row we loaded
            r = requests.patch(
                SUPABASE_URL + "/rest/v1/brain?id=eq." + str(brain_id),
                headers=HEADERS, json=payload
            )
            result = r.json()
            print("  -> Brain saved (id=" + str(brain_id) + ") rules=" + str(len(b.get("rulebook",[]))))
        else:
            # No existing row — insert new
            result = sb_insert("brain", payload)
            if isinstance(result, list) and len(result) > 0:
                b["id"] = result[0]["id"]
            print("  -> Brain created, rules=" + str(len(b.get("rulebook",[]))))
    except Exception as e:
        print("  -> Brain save ERROR: " + str(e))

# ─────────────────────────────────────────────
# COMMANDS — owner talks to bot
# ─────────────────────────────────────────────

def process_commands(brain):
    try:
        pending = sb_select("commands", "?status=eq.PENDING&order=created_at.asc&limit=5")
        if not isinstance(pending, list) or len(pending) == 0:
            print("  -> No pending commands")
            return False  # no commands processed

        for cmd in pending:
            text = cmd.get("command", "").strip()
            if not text:
                sb_update("commands", "id", cmd["id"], {"status": "DONE", "response": "Empty command ignored."})
                continue

            print("  -> Command: " + text)

            rulebook_txt = "\n".join(["  " + str(i+1) + ". " + r
                                      for i, r in enumerate(brain.get("rulebook", [])[-20:])]) or "  None yet"
            strategy_txt = brain.get("strategy_notes", "") or "None yet"

            prompt = (
                "You are the brain of a BTC trading bot. Your owner sent you this instruction:\n\n"
                "INSTRUCTION: " + text + "\n\n"
                "Current rulebook:\n" + rulebook_txt + "\n\n"
                "Current strategy:\n" + strategy_txt + "\n\n"
                "You MUST obey this instruction. Respond in JSON only (no markdown, no backticks, no extra text):\n"
                "{\n"
                "  \"response\": \"1-2 sentences confirming exactly what you will now do differently\",\n"
                "  \"rule_to_add\": \"The exact trading rule to enforce from this instruction. Write null if not applicable.\",\n"
                "  \"rule_to_remove_keyword\": \"A keyword from an existing rule to delete. Write null if not applicable.\",\n"
                "  \"strategy_update\": \"Full rewritten strategy if this changes overall approach. Write null if not applicable.\"\n"
                "}"
            )

            try:
                msg = claude.messages.create(
                    model="claude-opus-4-6",
                    max_tokens=500,
                    messages=[{"role": "user", "content": prompt}]
                )
                raw  = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
                data = json.loads(raw)

                rule_added   = False
                rule_removed = False

                if data.get("rule_to_add") and data["rule_to_add"] not in ("null", "", None):
                    rule_clean = data["rule_to_add"].replace("[OWNER] ", "").replace("[OWNER]", "").strip()
                    brain["rulebook"].append("[OWNER] " + rule_clean)
                    rule_added = True

                if data.get("rule_to_remove_keyword") and data["rule_to_remove_keyword"] not in ("null", "", None):
                    kw     = data["rule_to_remove_keyword"].lower()
                    before = len(brain["rulebook"])
                    brain["rulebook"] = [r for r in brain["rulebook"] if kw not in r.lower()]
                    rule_removed = (len(brain["rulebook"]) < before)

                if data.get("strategy_update") and data["strategy_update"] not in ("null", "", None):
                    brain["strategy_notes"] = data["strategy_update"]

                brain["total_adjustments"] = brain.get("total_adjustments", 0) + 1
                response_text = data.get("response", "Command received.")

            except Exception as e:
                print("  -> Command Claude error: " + str(e))
                # Fallback: manually parse simple commands
                tl = text.lower()
                if "aggressive" in tl:
                    brain["rulebook"].append("[OWNER] Be aggressive — always trade UP or DOWN, minimize WAIT signals")
                    response_text = "Got it. I will be more aggressive and minimize WAIT signals."
                elif "conservative" in tl or "careful" in tl:
                    brain["rulebook"].append("[OWNER] Be conservative — only trade with very strong signals, prefer WAIT when uncertain")
                    response_text = "Got it. I will be more conservative and only trade on very strong setups."
                elif "pause" in tl or "stop" in tl:
                    brain["rulebook"].append("[OWNER] PAUSED — output WAIT on every signal until owner says resume")
                    response_text = "Understood. I will output WAIT on every signal until you tell me to resume."
                elif "resume" in tl:
                    brain["rulebook"] = [r for r in brain["rulebook"] if "PAUSED" not in r]
                    response_text = "Resumed. I will start trading normally again."
                else:
                    brain["rulebook"].append("[OWNER] " + text)
                    response_text = "Command noted and added to my rulebook."

            sb_update("commands", "id", cmd["id"], {"status": "DONE", "response": response_text})
            print("  -> Replied: " + response_text)
            print("  -> Rulebook now has " + str(len(brain.get("rulebook",[]))) + " rules:")
            for i, r in enumerate(brain.get("rulebook", [])[-5:]):
                print("     " + str(i+1) + ". " + r[:80])

        # Save brain immediately so commands are never lost
        save_brain(brain)
        print("  -> Commands fully processed and saved")
        return True  # commands were processed

    except Exception as e:
        print("  -> Command error: " + str(e))
        return False

# ─────────────────────────────────────────────
# DEEP REFLECT — real lessons after every trade
# ─────────────────────────────────────────────

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

        wins   = brain.get("total_wins",   0)
        losses = brain.get("total_losses", 0)
        total  = wins + losses
        wr     = round(wins / total * 100, 1) if total > 0 else 0

        recent     = [t for t in all_history[:20] if t.get("outcome") in ["WIN", "LOSS"]]
        recent_txt = "\n".join([
            t["signal"] + " | RSI:" + str(t.get("rsi","?")) +
            " | BB:" + str(t.get("bb_position","?")) +
            " | Mom:" + str(t.get("momentum_1h","?")) +
            " | Vol:" + str(t.get("vol_spike","?")) +
            " | " + t.get("outcome","?")
            for t in recent
        ]) or "No history yet"

        current_rules    = "\n".join(brain.get("rulebook",       [])) or "None yet"
        current_strategy = brain.get("strategy_notes", "")            or "None yet"
        current_persona  = brain.get("personality",    "")            or "None yet"

        if outcome == "WIN":
            pattern_instruction = "win_pattern: Describe exactly what conditions made this a winner. Be specific about RSI, BB, trend, momentum so it can be replicated. loss_pattern: write the string null"
        else:
            pattern_instruction = "loss_pattern: Describe exactly why this failed and what to avoid next time. Be specific. win_pattern: write the string null"

        prompt = (
            "You are the brain of an elite BTC trading bot. A trade just completed.\n"
            "Write REAL actionable insights, not raw numbers.\n\n"
            "TRADE: Signal=" + signal + " Outcome=" + outcome + "\n"
            "Entry=$" + str(entry) + " Exit=$" + str(exit_p) + " Move=$" + str(move) + "\n"
            "RSI=" + str(rsi) + " BB=" + str(bb) + "% Vol=" + str(vol) + "x Mom=" + str(mom) + "%\n"
            "Record: " + str(wins) + "W/" + str(losses) + "L (" + str(wr) + "% win rate)\n\n"
            "RECENT TRADES:\n" + recent_txt + "\n\n"
            "RULEBOOK:\n" + current_rules + "\n\n"
            "STRATEGY:\n" + current_strategy + "\n\n"
            "PERSONALITY:\n" + current_persona + "\n\n"
            "Respond in JSON only (no markdown, no backticks):\n"
            "{\n"
            "  \"new_rule\": \"One precise actionable rule from this trade. Example: When RSI above 70 and BB above 80 percent and momentum negative, DOWN is high probability. Be specific with numbers.\",\n"
            "  \"" + ("win_pattern" if outcome == "WIN" else "loss_pattern") + "\": \"" + ("Exact conditions that made this a winner. Specific RSI, BB, trend, volume details." if outcome == "WIN" else "Exact conditions that caused this loss. What warning signs to avoid.") + "\",\n"
            "  \"" + ("loss_pattern" if outcome == "WIN" else "win_pattern") + "\": null,\n"
            "  \"memory_note\": \"One key market insight to remember. Max 15 words.\",\n"
            "  \"strategy_update\": \"Rewrite full strategy in 3-5 sentences based on all results so far.\",\n"
            "  \"personality_update\": \"2-3 sentences on trading style, risk tolerance, best setups.\"\n"
            "}"
        )

        msg  = claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw  = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)

        def not_null(v):
            return v and str(v).strip().lower() not in ("null", "none", "")

        if not_null(data.get("new_rule")):
            brain["rulebook"].append(data["new_rule"])
        if not_null(data.get("win_pattern")):
            brain["win_patterns"].append(data["win_pattern"])
        if not_null(data.get("loss_pattern")):
            brain["loss_patterns"].append(data["loss_pattern"])
        if not_null(data.get("memory_note")):
            brain["market_memory"].append(data["memory_note"])
        if not_null(data.get("strategy_update")):
            brain["strategy_notes"] = data["strategy_update"]
        if not_null(data.get("personality_update")):
            brain["personality"] = data["personality_update"]

        brain["total_adjustments"] = brain.get("total_adjustments", 0) + 1
        print("  -> Rule: " + str(data.get("new_rule", ""))[:80])
        print("  -> Strategy updated")

    except Exception as e:
        print("  -> Reflect error: " + str(e))
        # Robust fallback — always writes readable English
        signal = trade.get("signal", "")
        rsi    = float(trade.get("rsi")          or 50)
        bb     = float(trade.get("bb_position")  or 50)
        vol    = float(trade.get("vol_spike")    or 1)
        mom    = float(trade.get("momentum_1h")  or 0)

        if outcome == "LOSS":
            if   signal == "UP"   and rsi > 65:
                brain["rulebook"].append("Avoid UP signals when RSI above 65 — overbought and likely to reverse")
            elif signal == "DOWN" and rsi < 35:
                brain["rulebook"].append("Avoid DOWN signals when RSI below 35 — oversold and likely to bounce")
            elif vol < 0.5:
                brain["rulebook"].append("Skip trades when volume is below 0.5x average — weak conviction leads to losses")
            elif signal == "UP"   and bb > 80:
                brain["rulebook"].append("Avoid UP when Bollinger Band position above 80 percent — price extended, reversal likely")
            elif signal == "DOWN" and bb < 20:
                brain["rulebook"].append("Avoid DOWN when Bollinger Band position below 20 percent — price extended, bounce likely")
            elif mom < 0 and signal == "UP":
                brain["rulebook"].append("Avoid UP when momentum is negative — momentum must confirm direction")
            elif mom > 0 and signal == "DOWN":
                brain["rulebook"].append("Avoid DOWN when momentum is positive — momentum must confirm direction")
            else:
                brain["rulebook"].append("Wait for stronger multi-timeframe confluence before committing to a direction")
            brain["loss_patterns"].append(
                signal + " failed with RSI " + str(round(rsi,1)) + ", BB " + str(round(bb,1)) +
                "%, momentum " + str(round(mom,3)) + "%, vol " + str(round(vol,2)) + "x — note these conditions"
            )
        else:
            if signal == "DOWN" and rsi > 60 and bb > 65:
                brain["win_patterns"].append("DOWN wins when RSI above 60 and price in upper Bollinger Band — overbought reversal setup works")
            elif signal == "UP" and rsi < 40 and bb < 35:
                brain["win_patterns"].append("UP wins when RSI below 40 and price in lower Bollinger Band — oversold bounce setup works")
            elif signal == "DOWN" and mom < -0.1:
                brain["win_patterns"].append("DOWN wins when momentum is clearly negative — follow the momentum")
            elif signal == "UP" and mom > 0.1:
                brain["win_patterns"].append("UP wins when momentum is clearly positive — follow the momentum")
            else:
                brain["win_patterns"].append(
                    signal + " won: RSI " + str(round(rsi,1)) + ", BB " + str(round(bb,1)) +
                    "%, mom " + str(round(mom,3)) + "% — conditions showed clear directional bias"
                )
        brain["total_adjustments"] = brain.get("total_adjustments", 0) + 1

# ─────────────────────────────────────────────
# MARKET DATA
# ─────────────────────────────────────────────

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
    signal = calc_ema(
        [calc_ema(closes[:i+1], 12) - calc_ema(closes[:i+1], 26) for i in range(26, len(closes))], 9
    )
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
    hi, lo  = max(highs), min(lows)
    return round((close - lo) / (hi - lo) * 100, 1) if hi != lo else 50

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
    if lw > body*2 and uw < body*0.5:             patterns.append("HAMMER_BULL")
    if uw > body*2 and lw < body*0.5:             patterns.append("SHOOT_STAR_BEAR")
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
    candles    = get_candles(interval, limit)
    closes     = [c["close"]  for c in candles]
    volumes    = [c["volume"] for c in candles]
    rsi        = calc_rsi(closes)
    macd, ms, mh = calc_macd(closes)
    bbu, bbl, bbp = calc_bollinger(closes)
    stoch      = calc_stochastic(candles)
    atr        = calc_atr(candles)
    vwap       = calc_vwap(candles)
    avg_v      = sum(volumes[-20:-1]) / 19 if len(volumes) > 20 else sum(volumes) / len(volumes)
    vol_spike  = round(volumes[-1] / avg_v, 2) if avg_v > 0 else 1.0
    mom        = round(((closes[-1] - closes[-4]) / closes[-4]) * 100, 3) if len(closes) >= 4 else 0
    sma20      = sum(closes[-20:]) / 20
    return {
        "label":         label,
        "close":         round(closes[-1], 2),
        "rsi":           rsi,
        "macd_hist":     mh,
        "bb_pos":        bbp,
        "stoch":         stoch,
        "atr":           atr,
        "vwap":          vwap,
        "price_vs_vwap": "ABOVE" if closes[-1] > vwap else "BELOW",
        "vol_spike":     vol_spike,
        "momentum":      mom,
        "trend":         "UP" if closes[-1] > sma20 else "DOWN",
        "patterns":      detect_patterns(candles),
        "candles":       "".join(["G" if c["close"] > c["open"] else "R" for c in candles[-5:]])
    }

def get_fear_greed():
    try:
        r    = requests.get("https://api.alternative.me/fng/?limit=3", timeout=5)
        data = r.json()["data"]
        return [{"value": d["value"], "label": d["value_classification"]} for d in data]
    except:
        return [{"value": "N/A", "label": "N/A"}]

def get_funding():
    try:
        r    = requests.get("https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1", timeout=5)
        rate = float(r.json()[0]["fundingRate"]) * 100
        s    = "OVER_LONG" if rate > 0.05 else "OVER_SHORT" if rate < -0.05 else "NEUTRAL"
        return {"rate": round(rate, 4), "sentiment": s}
    except:
        return {"rate": "N/A", "sentiment": "N/A"}

def get_orderbook():
    try:
        r     = requests.get("https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=20", timeout=5)
        d     = r.json()
        bv    = sum(float(b[1]) for b in d["bids"])
        av    = sum(float(a[1]) for a in d["asks"])
        ratio = round(bv / av, 2) if av > 0 else 1.0
        return {"ratio": ratio, "pressure": "BUY" if ratio > 1.2 else "SELL" if ratio < 0.8 else "BALANCED"}
    except:
        return {"ratio": "N/A", "pressure": "N/A"}

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
    except:
        return {"change_24h": "N/A", "high_24h": "N/A", "low_24h": "N/A", "vol_24h_m": "N/A"}

def get_news():
    # Try multiple sources so news is never empty
    try:
        r = requests.get(
            "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&categories=BTC&sortOrder=latest",
            timeout=5
        )
        items = r.json().get("Data", [])[:6]
        if items:
            return [item["title"] for item in items]
    except: pass
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        val = r.json()["data"][0]
        return ["Fear & Greed: " + val["value"] + " (" + val["value_classification"] + ") — market sentiment snapshot"]
    except: pass
    return ["News unavailable"]

# ─────────────────────────────────────────────
# HISTORY & STATS
# ─────────────────────────────────────────────

def get_history():
    r = sb_select("signals", "?order=created_at.desc&limit=200")
    return r if isinstance(r, list) else []

def calc_stats(history):
    resolved = [t for t in history if t.get("outcome") in ["WIN", "LOSS"]]
    if not resolved:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "streak": 0, "streak_type": "N/A", "last_10": []}
    wins   = sum(1 for t in resolved if t["outcome"] == "WIN")
    losses = len(resolved) - wins
    streak = 0
    first  = resolved[0]["outcome"]
    for t in resolved:
        if t["outcome"] == first: streak += 1
        else: break
    return {
        "total":       len(resolved),
        "wins":        wins,
        "losses":      losses,
        "win_rate":    round(wins / len(resolved) * 100, 1),
        "streak":      streak,
        "streak_type": first,
        "last_10":     [t["outcome"] for t in resolved[:10]]
    }

# ─────────────────────────────────────────────
# RESOLVE — score last signal, must be 15+ min old
# ─────────────────────────────────────────────

def resolve_and_learn(current_price, brain, all_history):
    pending = sb_select("signals", "?outcome=eq.PENDING&order=created_at.asc&limit=1")
    if not isinstance(pending, list) or len(pending) == 0:
        print("  -> No pending signals to resolve")
        return

    last = pending[0]

    # Only resolve if signal is at least 14 minutes old
    try:
        created = datetime.datetime.fromisoformat(last["created_at"].replace("Z", "+00:00"))
        age_mins = (utcnow() - created).total_seconds() / 60
        if age_mins < 14:
            print("  -> Signal too recent (" + str(round(age_mins, 1)) + " min) — waiting for 15-min window")
            return
    except Exception as e:
        print("  -> Age check error: " + str(e))

    last_price = float(last["btc_price"])
    signal     = last["signal"]

    if   signal == "WAIT":                                outcome = "SKIPPED"
    elif signal == "UP"   and current_price > last_price: outcome = "WIN"
    elif signal == "DOWN" and current_price < last_price: outcome = "WIN"
    else:                                                 outcome = "LOSS"

    sb_update("signals", "id", last["id"], {"outcome": outcome, "exit_price": current_price})
    print("  Resolved: " + signal + " -> " + outcome +
          " ($" + str(round(last_price, 2)) + " -> $" + str(round(current_price, 2)) + ")")

    if outcome in ["WIN", "LOSS"]:
        brain["total_trades"] = brain.get("total_trades", 0) + 1
        if outcome == "WIN":
            brain["total_wins"]   = brain.get("total_wins",   0) + 1
        else:
            brain["total_losses"] = brain.get("total_losses", 0) + 1
        print("  -> Learning from this trade...")
        deep_reflect(last, outcome, brain, all_history)

# ─────────────────────────────────────────────
# ASK CLAUDE — full memory + market context
# ─────────────────────────────────────────────

def ask_claude(tf1m, tf5m, tf15m, tf1h, tf4h, fg, funding, ob, mstats, news, history, stats, brain):

    # Separate owner rules from learned rules for emphasis
    all_rules     = brain.get("rulebook", [])
    owner_rules   = [r for r in all_rules if r.startswith("[OWNER]")]
    learned_rules = [r for r in all_rules if not r.startswith("[OWNER]")]

    owner_txt   = "\n".join(["  !! " + r for r in owner_rules[-10:]]) or "  None"
    learned_txt = "\n".join(["  " + str(i+1) + ". " + r for i, r in enumerate(learned_rules[-15:])]) or "  None yet"
    win_txt     = "\n".join(["  + " + p for p in brain.get("win_patterns",  [])[-10:]]) or "  None yet"
    loss_txt    = "\n".join(["  - " + p for p in brain.get("loss_patterns", [])[-10:]]) or "  None yet"
    memory_txt  = "\n".join(["  * " + m for m in brain.get("market_memory", [])[-8:]]) or "  None yet"
    strategy    = brain.get("strategy_notes", "") or "Still developing strategy."
    personality = brain.get("personality",    "") or "Still developing style."

    resolved = [t for t in history if t.get("outcome") in ["WIN", "LOSS"]][:30]
    hist_txt = "\n".join([
        "  " + t["created_at"][:16] + " | " + t["signal"] +
        " | RSI:" + str(t.get("rsi","?")) +
        " | BB:"  + str(t.get("bb_position","?")) +
        " | Mom:" + str(t.get("momentum_1h","?")) +
        " | Vol:" + str(t.get("vol_spike","?")) +
        " | "     + t["outcome"]
        for t in resolved
    ]) or "  No resolved trades yet"

    fg_trend = " -> ".join([str(f["value"]) + "(" + f["label"] + ")" for f in fg[:3]])
    news_txt = "\n".join(["  - " + h for h in news[:5]])
    last_10  = " ".join(stats["last_10"]) or "None yet"

    prompt = (
        "You are an elite self-learning BTC trading AI with persistent memory.\n"
        "Predict if BTC will be HIGHER or LOWER in exactly 15 minutes on Kalshi.\n\n"

        "════ OWNER COMMANDS (MANDATORY — obey these above everything else) ════\n"
        + owner_txt + "\n\n"

        "════ YOUR STRATEGY ════\n"
        + strategy + "\n\n"

        "════ YOUR PERSONALITY ════\n"
        + personality + "\n\n"

        "════ YOUR LEARNED RULES ════\n"
        + learned_txt + "\n\n"

        "════ WIN PATTERNS ════\n"
        + win_txt + "\n\n"

        "════ LOSS PATTERNS ════\n"
        + loss_txt + "\n\n"

        "════ MARKET MEMORY ════\n"
        + memory_txt + "\n\n"

        "════ LIVE MARKET ════\n"
        "BTC: $" + str(tf15m["close"]) + "\n\n"
        "TIMEFRAMES:\n"
        "1m:  RSI=" + str(tf1m["rsi"])  + " Stoch=" + str(tf1m["stoch"])  + " MACD=" + str(tf1m["macd_hist"])  + " BB=" + str(tf1m["bb_pos"])  + "% Vol=" + str(tf1m["vol_spike"])  + "x Mom=" + str(tf1m["momentum"])  + "% Trend=" + tf1m["trend"]  + " C=" + tf1m["candles"]  + " P=" + ",".join(tf1m["patterns"]) + "\n"
        "5m:  RSI=" + str(tf5m["rsi"])  + " Stoch=" + str(tf5m["stoch"])  + " MACD=" + str(tf5m["macd_hist"])  + " BB=" + str(tf5m["bb_pos"])  + "% Vol=" + str(tf5m["vol_spike"])  + "x Mom=" + str(tf5m["momentum"])  + "% Trend=" + tf5m["trend"]  + " C=" + tf5m["candles"]  + " P=" + ",".join(tf5m["patterns"]) + "\n"
        "15m: RSI=" + str(tf15m["rsi"]) + " Stoch=" + str(tf15m["stoch"]) + " MACD=" + str(tf15m["macd_hist"]) + " BB=" + str(tf15m["bb_pos"]) + "% Vol=" + str(tf15m["vol_spike"]) + "x Mom=" + str(tf15m["momentum"]) + "% Trend=" + tf15m["trend"] + " C=" + tf15m["candles"] + " P=" + ",".join(tf15m["patterns"]) + "\n"
        "1h:  RSI=" + str(tf1h["rsi"])  + " MACD=" + str(tf1h["macd_hist"])  + " BB=" + str(tf1h["bb_pos"])  + "% Mom=" + str(tf1h["momentum"])  + "% Trend=" + tf1h["trend"]  + " C=" + tf1h["candles"] + "\n"
        "4h:  RSI=" + str(tf4h["rsi"])  + " MACD=" + str(tf4h["macd_hist"])  + " BB=" + str(tf4h["bb_pos"])  + "% Mom=" + str(tf4h["momentum"])  + "% Trend=" + tf4h["trend"]  + " C=" + tf4h["candles"] + "\n\n"

        "SENTIMENT:\n"
        "F&G (3-day): " + fg_trend + "\n"
        "Funding: " + str(funding["rate"]) + "% (" + funding["sentiment"] + ")\n"
        "Order Book: " + ob["pressure"] + " (ratio=" + str(ob["ratio"]) + ")\n"
        "24h: " + str(mstats.get("change_24h","N/A")) + "% | H=$" + str(mstats.get("high_24h","N/A")) + " L=$" + str(mstats.get("low_24h","N/A")) + "\n\n"

        "NEWS:\n" + news_txt + "\n\n"

        "PERFORMANCE:\n"
        "Win Rate: " + str(stats["win_rate"]) + "% | " + str(stats["wins"]) + "W/" + str(stats["losses"]) + "L | "
        "Streak: " + str(stats["streak"]) + "x " + stats["streak_type"] + "\n"
        "Last 10: " + last_10 + "\n\n"

        "RECENT TRADES:\n" + hist_txt + "\n\n"

        "════ DECISION PROCESS ════\n"
        "Step 1: Check OWNER COMMANDS — if any apply, you MUST follow them, no exceptions.\n"
        "Step 2: Apply your learned rules to this market setup.\n"
        "Step 3: Read all 5 timeframes — do 3 or more agree on direction?\n"
        "Step 4: Check sentiment — overleveraged long = DOWN pressure, overleveraged short = UP pressure.\n"
        "Step 5: Only output WAIT if truly no edge. Prefer UP or DOWN.\n\n"
        "Reply with ONLY one word: UP, DOWN, or WAIT"
    )

    msg = claude.messages.create(
        model="claude-opus-4-6",
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip().upper()
    if "DOWN" in raw: return "DOWN"
    if "UP"   in raw: return "UP"
    return "WAIT"

# ─────────────────────────────────────────────
# SAVE SIGNAL
# ─────────────────────────────────────────────

def save_signal(signal, tf15m):
    record = {
        "signal":      signal,
        "btc_price":   tf15m["close"],
        "rsi":         tf15m["rsi"],
        "macd":        tf15m["macd_hist"],
        "momentum_1h": tf15m["momentum"],
        "vol_spike":   tf15m["vol_spike"],
        "bb_position": tf15m["bb_pos"],
        "outcome":     "PENDING",
        "created_at":  utcnow().isoformat()
    }
    result = sb_insert("signals", record)
    if isinstance(result, list) and len(result) > 0:
        return result[0]["id"]
    return "?"

# ─────────────────────────────────────────────
# KALSHI WINDOW
# ─────────────────────────────────────────────

def is_kalshi_window():
    return utcnow().minute % 15 == 0

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    now = utcnow().strftime("%Y-%m-%d %H:%M")
    print("══════════════════════════════════════════")
    print("  BTC ORACLE  " + now + " UTC")
    print("══════════════════════════════════════════")

    # 1. Load brain
    print("  -> Loading brain...")
    brain  = load_brain()
    wins   = brain.get("total_wins",   0)
    losses = brain.get("total_losses", 0)
    total  = wins + losses
    wr     = round(wins / total * 100, 1) if total > 0 else 0
    print("  -> " + str(len(brain.get("rulebook",[]))) + " rules | " +
          str(brain.get("total_adjustments", 0)) + " reflections | " +
          str(wr) + "% win rate")

    # 2. Owner commands — saved to brain immediately inside process_commands
    print("  -> Checking commands...")
    process_commands(brain)

    # 3. Market data
    print("  -> Analyzing 5 timeframes...")
    tf1m  = analyze_tf("1m",  60, "1M")
    tf5m  = analyze_tf("5m",  60, "5M")
    tf15m = analyze_tf("15m", 60, "15M")
    tf1h  = analyze_tf("1h",  60, "1H")
    tf4h  = analyze_tf("4h",  60, "4H")
    print("  -> $" + str(tf15m["close"]) + " | RSI:" + str(tf15m["rsi"]) +
          " | Trend:" + tf15m["trend"] + " | BB:" + str(tf15m["bb_pos"]) + "%")

    # 4. Sentiment
    print("  -> Getting sentiment...")
    fg      = get_fear_greed()
    funding = get_funding()
    ob      = get_orderbook()
    mstats  = get_market_stats()
    news    = get_news()
    print("  -> F&G:" + str(fg[0]["value"]) +
          " | Funding:" + str(funding["rate"]) +
          "% | OB:" + ob["pressure"])

    # 5. History + resolve last trade (only if 15+ min old)
    print("  -> Loading history...")
    history = get_history()
    stats   = calc_stats(history)

    print("  -> Resolving last signal...")
    resolve_and_learn(tf15m["close"], brain, history)

    # Refresh stats after resolution
    history = get_history()
    stats   = calc_stats(history)
    print("  -> Win rate: " + str(stats["win_rate"]) + "% (" +
          str(stats["wins"]) + "W/" + str(stats["losses"]) + "L)")

    # 6. Signal — only on :00, :15, :30, :45
    if is_kalshi_window():

        # Check for direct owner override rules first — obey instantly, no Claude needed
        owner_rules = [r for r in brain.get("rulebook", []) if r.startswith("[OWNER]")]
        forced_signal = None
        for rule in owner_rules:
            rl = rule.lower()
            if "pause" in rl or "wait on every" in rl or "output wait" in rl:
                forced_signal = "WAIT"
                print("  -> OWNER OVERRIDE: PAUSED — outputting WAIT")
                break
            elif "only up" in rl or "only trade up" in rl:
                forced_signal = "UP"
                print("  -> OWNER OVERRIDE: only UP")
                break
            elif "only down" in rl or "only trade down" in rl:
                forced_signal = "DOWN"
                print("  -> OWNER OVERRIDE: only DOWN")
                break

        if forced_signal:
            signal = forced_signal
        else:
            print("  -> Kalshi window — asking Claude...")
            signal = ask_claude(tf1m, tf5m, tf15m, tf1h, tf4h,
                                fg, funding, ob, mstats, news,
                                history, stats, brain)

        print("")
        print("  ══════════════════════")
        print("  SIGNAL:  " + signal)
        print("  ══════════════════════")
        print("")
        sid = save_signal(signal, tf15m)
        print("  -> Saved (id: " + str(sid) + ")")
    else:
        mins = 15 - (utcnow().minute % 15)
        print("  -> Monitoring — next Kalshi window in " + str(mins) + " min")

    # 7. Save brain (always at end — captures reflection + any new learning)
    save_brain(brain)
    print("  Done.")

main()
