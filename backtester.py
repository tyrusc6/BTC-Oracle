"""
BTC Oracle - Backtester V2
Uses the scoring model (not Claude) for instant, free backtesting.
Simulates regime detection, WAIT filter, and all indicators.
"""

import os
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
import requests


# ========== DATA FETCHING ==========

def fetch_all_historical(days=14):
    """Pull historical 15-min candles from Kraken."""
    print(f"Fetching {days} days of BTC data from Kraken...")
    all_candles = []
    end_time = int(datetime.now(timezone.utc).timestamp())
    start_time = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    since = start_time

    while since < end_time:
        url = f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=15&since={since}"
        try:
            resp = requests.get(url, timeout=15)
            data = resp.json()
            if data.get("error") and len(data["error"]) > 0:
                break
            candles = data["result"].get("XXBTZUSD", [])
            if not candles:
                break
            all_candles.extend(candles)
            last_ts = int(candles[-1][0])
            if last_ts <= since:
                break
            since = last_ts
            dt = datetime.fromtimestamp(last_ts, tz=timezone.utc)
            print(f"  {len(all_candles)} candles (up to {dt.strftime('%Y-%m-%d %H:%M')})")
            time.sleep(1)
        except Exception as e:
            print(f"  Error: {e}")
            time.sleep(3)

    if not all_candles:
        return pd.DataFrame()

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "vwap", "volume", "count"])
    for col in ["open", "high", "low", "close", "vwap", "volume"]:
        df[col] = df[col].astype(float)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    print(f"Total: {len(df)} candles | {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")
    return df


# ========== INDICATOR CALCULATIONS ==========

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    ag = np.mean(gains[:period])
    al = np.mean(losses[:period])
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100 - (100 / (1 + ag / al))


def calc_macd(prices):
    if len(prices) < 35:
        return None, None, None
    s = pd.Series(prices)
    ml = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    sl = ml.ewm(span=9, adjust=False).mean()
    return float(ml.iloc[-1]), float(sl.iloc[-1]), float((ml - sl).iloc[-1])


def calc_bollinger_pos(prices, period=20):
    if len(prices) < period:
        return None
    s = pd.Series(prices)
    mid = float(s.rolling(period).mean().iloc[-1])
    std = float(s.rolling(period).std().iloc[-1])
    if std == 0:
        return 0.5
    upper = mid + 2 * std
    lower = mid - 2 * std
    if upper == lower:
        return 0.5
    return (prices[-1] - lower) / (upper - lower)


def calc_ema(prices, period):
    if len(prices) < period:
        return None
    return float(pd.Series(prices).ewm(span=period, adjust=False).mean().iloc[-1])


def detect_trend(prices):
    if len(prices) < 20:
        return "UNKNOWN", 0
    current = prices[-1]
    sma10 = np.mean(prices[-10:])
    sma20 = np.mean(prices[-20:])
    slope = np.polyfit(np.arange(20), prices[-20:], 1)[0]
    hh = max(prices[-5:]) > max(prices[-10:-5])
    hl = min(prices[-5:]) > min(prices[-10:-5])
    pct = ((prices[-1] - prices[-min(30, len(prices))]) / prices[-min(30, len(prices))]) * 100

    score = 0
    score += 1 if current > sma10 else -1
    score += 1 if current > sma20 else -1
    score += 1 if sma10 > sma20 else -1
    score += 1 if slope > 0 else -1
    score += 1 if hh and hl else (-1 if not hh and not hl else 0)

    if score >= 3: return "STRONG_UPTREND", pct
    elif score >= 1: return "UPTREND", pct
    elif score <= -3: return "STRONG_DOWNTREND", pct
    elif score <= -1: return "DOWNTREND", pct
    return "SIDEWAYS", pct


def detect_regime(prices):
    if len(prices) < 30:
        return "UNKNOWN"
    returns = np.diff(prices) / prices[:-1]
    vol = np.std(returns[-20:]) * 100
    avg_vol = np.std(returns) * 100

    changes = np.diff(prices)
    up = np.where(changes > 0, changes, 0)
    down = np.where(changes < 0, -changes, 0)
    au = np.mean(up[-14:])
    ad = np.mean(down[-14:])
    strength = abs(au - ad) / (au + ad) if (au + ad) > 0 else 0

    sma10 = np.mean(prices[-10:])
    sma30 = np.mean(prices[-30:])
    slope = np.polyfit(np.arange(20), prices[-20:], 1)[0]

    if vol > avg_vol * 2:
        return "HIGH_VOLATILITY"
    elif sma10 > sma30 and slope > 0 and strength > 0.3:
        return "TRENDING_UP"
    elif sma10 < sma30 and slope < 0 and strength > 0.3:
        return "TRENDING_DOWN"
    elif strength < 0.15:
        return "RANGING"
    elif sma10 > sma30:
        return "WEAK_UPTREND"
    elif sma10 < sma30:
        return "WEAK_DOWNTREND"
    return "CHOPPY"


# ========== SCORING MODEL ==========

def score_at_index(df, idx):
    """Calculate score for a given candle index."""
    if idx < 35:
        return None, None, None, None, None

    prices = df["close"].values[:idx + 1].astype(float)
    highs = df["high"].values[:idx + 1].astype(float)
    lows = df["low"].values[:idx + 1].astype(float)
    volumes = df["volume"].values[:idx + 1].astype(float)

    # Use last 200 candles max
    p = prices[-200:]
    h = highs[-200:]
    l = lows[-200:]
    v = volumes[-200:]

    current = p[-1]
    votes = []

    # Trend (highest weight)
    trend_short, _ = detect_trend(p[-50:] if len(p) >= 50 else p)
    trend_long, _ = detect_trend(p[-100:] if len(p) >= 100 else p)

    trend_map = {"STRONG_UPTREND": (+1, 1.5), "UPTREND": (+1, 1.0), "STRONG_DOWNTREND": (-1, 1.5), "DOWNTREND": (-1, 1.0)}
    trend_long_map = {"STRONG_UPTREND": (+1, 1.8), "UPTREND": (+1, 1.2), "STRONG_DOWNTREND": (-1, 1.8), "DOWNTREND": (-1, 1.2)}

    if trend_short in trend_map:
        votes.append(trend_map[trend_short])
    if trend_long in trend_long_map:
        votes.append(trend_long_map[trend_long])

    # RSI
    rsi = calc_rsi(p)
    if rsi is not None:
        if rsi < 30: votes.append((+1, 0.8))
        elif rsi > 70: votes.append((-1, 0.8))
        elif rsi < 45: votes.append((+1, 0.35))
        elif rsi > 55: votes.append((-1, 0.35))

    # MACD
    macd, macd_sig, macd_hist = calc_macd(p)
    if macd is not None:
        votes.append((+1 if macd > 0 else -1, 0.5))
    if macd_hist is not None:
        votes.append((+1 if macd_hist > 0 else -1, 0.5))

    # Momentum
    if len(p) >= 11:
        mom = p[-1] - p[-11]
        votes.append((+1 if mom > 0 else -1, 0.55))

    # EMA crossover
    ema9 = calc_ema(p, 9)
    ema21 = calc_ema(p, 21)
    if ema9 is not None and ema21 is not None:
        votes.append((+1 if ema9 > ema21 else -1, 0.5))

    # Bollinger position
    bb = calc_bollinger_pos(p)
    if bb is not None:
        if bb < 0.2: votes.append((+1, 0.6))
        elif bb > 0.8: votes.append((-1, 0.6))
        elif bb < 0.4: votes.append((+1, 0.3))
        elif bb > 0.6: votes.append((-1, 0.3))

    # VWAP
    if np.sum(v[-50:]) > 0:
        vwap = np.sum(p[-50:] * v[-50:]) / np.sum(v[-50:])
        votes.append((+1 if current > vwap else -1, 0.4))

    # OBV trend
    if len(p) >= 10:
        obv = [0]
        for i in range(1, len(p[-20:])):
            vi = v[-20:][i] if v[-20:][i] > 0 else 0
            if p[-20:][i] > p[-20:][i-1]: obv.append(obv[-1] + vi)
            elif p[-20:][i] < p[-20:][i-1]: obv.append(obv[-1] - vi)
            else: obv.append(obv[-1])
        if len(obv) >= 10:
            rising = np.mean(obv[-5:]) > np.mean(obv[-10:-5])
            votes.append((+1 if rising else -1, 0.5))

    # ROC
    if len(p) >= 11:
        roc = ((p[-1] - p[-11]) / p[-11]) * 100
        if roc > 0.1: votes.append((+1, 0.4))
        elif roc < -0.1: votes.append((-1, 0.4))

    if not votes:
        return None, None, None, None, None

    total_weight = sum(abs(w) for _, w in votes)
    weighted_sum = sum(d * w for d, w in votes)
    score = weighted_sum / total_weight if total_weight > 0 else 0

    signal = "UP" if score > 0 else "DOWN"
    confidence = min(0.95, 0.5 + abs(score) * 0.45)

    regime = detect_regime(p[-50:] if len(p) >= 50 else p)

    return score, confidence, signal, trend_short, regime


# ========== WAIT FILTER ==========

def should_trade(score, confidence, signal, trend_short, trend_long, regime):
    """Simulate the STRICT WAIT filter."""
    reasons_trade = 0
    reasons_wait = 0

    # Must have strong score (raised from 0.4 to 0.5)
    if abs(score) > 0.5:
        reasons_trade += 1
    elif abs(score) < 0.25:
        reasons_wait += 1

    # Must have high confidence (raised from 0.65 to 0.80)
    if confidence >= 0.80:
        reasons_trade += 1
    elif confidence < 0.70:
        reasons_wait += 1

    # Short trend must align with signal
    if signal == "UP" and "UPTREND" in trend_short:
        reasons_trade += 1
    elif signal == "DOWN" and "DOWNTREND" in trend_short:
        reasons_trade += 1
    elif "SIDEWAYS" in trend_short:
        reasons_wait += 1

    # Long trend must also align
    if signal == "UP" and "UPTREND" in trend_long:
        reasons_trade += 1
    elif signal == "DOWN" and "DOWNTREND" in trend_long:
        reasons_trade += 1
    else:
        reasons_wait += 1

    # Regime must support
    if regime in ("TRENDING_UP",) and signal == "UP":
        reasons_trade += 1
    elif regime in ("TRENDING_DOWN",) and signal == "DOWN":
        reasons_trade += 1
    elif regime in ("HIGH_VOLATILITY", "CHOPPY", "WEAK_UPTREND", "WEAK_DOWNTREND"):
        reasons_wait += 1

    # Need 4+ reasons AND more reasons to trade than wait
    return reasons_trade >= 4 and reasons_trade > reasons_wait


# ========== MAIN BACKTEST ==========

def run_backtest(days=14):
    print("=" * 60)
    print("BTC ORACLE - BACKTESTER V2 (Scoring Model)")
    print("Free, instant, no API costs")
    print("=" * 60)

    df = fetch_all_historical(days)
    if df.empty:
        print("No data!")
        return

    total = len(df)
    print(f"\nBacktesting {total} candles ({days} days)")
    print("=" * 60)

    all_results = []
    trade_results = []
    wait_results = []

    start = 50  # need history
    end = total - 1  # need next candle

    for i in range(start, end):
        score, conf, signal, trend, regime = score_at_index(df, i)
        if score is None:
            continue

        # Get longer trend for filter
        prices_long = df["close"].values[:i+1].astype(float)
        trend_long, _ = detect_trend(prices_long[-100:] if len(prices_long) >= 100 else prices_long)

        # Should we trade?
        trade = should_trade(score, conf, signal, trend, trend_long, regime)

        # Check outcome
        current_price = float(df.iloc[i]["close"])
        next_price = float(df.iloc[i + 1]["close"])
        went_up = next_price > current_price
        predicted_up = signal == "UP"
        outcome = "WIN" if went_up == predicted_up else "LOSS"
        change = next_price - current_price

        result = {
            "index": i,
            "price": current_price,
            "next_price": next_price,
            "signal": signal,
            "score": round(score, 4),
            "confidence": round(conf, 3),
            "trend": trend,
            "regime": regime,
            "traded": trade,
            "outcome": outcome,
            "change": change
        }
        all_results.append(result)
        if trade:
            trade_results.append(result)
        else:
            wait_results.append(result)

    # Print results
    print_report(all_results, trade_results, wait_results)

    # Save
    with open("backtest_v2_results.json", "w") as f:
        json.dump({
            "all": {"total": len(all_results), "wins": len([r for r in all_results if r["outcome"] == "WIN"]),
                    "win_rate": round(len([r for r in all_results if r["outcome"] == "WIN"]) / len(all_results) * 100, 2) if all_results else 0},
            "trades": {"total": len(trade_results), "wins": len([r for r in trade_results if r["outcome"] == "WIN"]),
                       "win_rate": round(len([r for r in trade_results if r["outcome"] == "WIN"]) / len(trade_results) * 100, 2) if trade_results else 0},
            "waits": {"total": len(wait_results), "wins": len([r for r in wait_results if r["outcome"] == "WIN"]),
                      "win_rate": round(len([r for r in wait_results if r["outcome"] == "WIN"]) / len(wait_results) * 100, 2) if wait_results else 0},
            "results": all_results[-50:]  # save last 50 for review
        }, f, indent=2)
    print("\nResults saved to backtest_v2_results.json")


def print_report(all_r, trade_r, wait_r):
    print("\n" + "=" * 60)
    print("BACKTEST REPORT")
    print("=" * 60)

    # All signals
    total = len(all_r)
    wins = len([r for r in all_r if r["outcome"] == "WIN"])
    print(f"\nALL SIGNALS: {wins}/{total} = {wins/total*100:.1f}% win rate" if total else "No signals")

    # Trade only
    t_total = len(trade_r)
    t_wins = len([r for r in trade_r if r["outcome"] == "WIN"])
    print(f"TRADE ONLY:  {t_wins}/{t_total} = {t_wins/t_total*100:.1f}% win rate" if t_total else "No trades")
    print(f"WAIT:        {len(wait_r)} signals skipped")
    print(f"SELECTIVITY: {t_total}/{total} = {t_total/total*100:.1f}% of signals traded" if total else "")

    if not trade_r:
        return

    # By confidence
    print("\n--- BY CONFIDENCE ---")
    for label, lo, hi in [("90%+", 0.9, 1.0), ("80-90%", 0.8, 0.9), ("70-80%", 0.7, 0.8), ("60-70%", 0.6, 0.7), ("<60%", 0, 0.6)]:
        subset = [r for r in trade_r if lo <= r["confidence"] < hi]
        if subset:
            sw = len([r for r in subset if r["outcome"] == "WIN"])
            print(f"  {label}: {sw}/{len(subset)} = {sw/len(subset)*100:.1f}%")

    # By regime
    print("\n--- BY REGIME (trades only) ---")
    regimes = set(r["regime"] for r in trade_r)
    for regime in sorted(regimes):
        subset = [r for r in trade_r if r["regime"] == regime]
        sw = len([r for r in subset if r["outcome"] == "WIN"])
        print(f"  {regime}: {sw}/{len(subset)} = {sw/len(subset)*100:.1f}%")

    # By direction
    print("\n--- BY DIRECTION (trades only) ---")
    up = [r for r in trade_r if r["signal"] == "UP"]
    down = [r for r in trade_r if r["signal"] == "DOWN"]
    if up:
        uw = len([r for r in up if r["outcome"] == "WIN"])
        print(f"  UP:   {uw}/{len(up)} = {uw/len(up)*100:.1f}%")
    if down:
        dw = len([r for r in down if r["outcome"] == "WIN"])
        print(f"  DOWN: {dw}/{len(down)} = {dw/len(down)*100:.1f}%")

    # By trend
    print("\n--- BY TREND (trades only) ---")
    trends = set(r["trend"] for r in trade_r)
    for t in sorted(trends):
        subset = [r for r in trade_r if r["trend"] == t]
        sw = len([r for r in subset if r["outcome"] == "WIN"])
        print(f"  {t}: {sw}/{len(subset)} = {sw/len(subset)*100:.1f}%")

    # Streaks
    max_win = max_loss = cur = 0
    for r in trade_r:
        if r["outcome"] == "WIN":
            cur = cur + 1 if cur > 0 else 1
            max_win = max(max_win, cur)
        else:
            cur = cur - 1 if cur < 0 else -1
            max_loss = max(max_loss, abs(cur))

    print(f"\n--- STREAKS ---")
    print(f"  Best win streak: {max_win}")
    print(f"  Worst loss streak: {max_loss}")

    # Avg move
    changes = [abs(r["change"]) for r in trade_r]
    print(f"\n--- PRICE MOVEMENT ---")
    print(f"  Avg 15m move: ${np.mean(changes):.2f}")
    print(f"  Max 15m move: ${max(changes):.2f}")

    print("\n" + "=" * 60)
    if t_total:
        print(f">>> BOTTOM LINE: {t_wins}/{t_total} = {t_wins/t_total*100:.1f}% on TRADE signals")
    print("=" * 60)


if __name__ == "__main__":
    run_backtest(days=14)
