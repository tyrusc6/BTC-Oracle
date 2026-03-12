"""
BTC Oracle - Backtester
Pulls historical BTC data from Kraken, calculates indicators for each
15-minute window, asks Claude for predictions, and scores results.

Usage: python3 backtester.py
"""

import os
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import requests
import anthropic

load_dotenv()

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# --- Config ---
DAYS_BACK = 14  # 2 weeks
INTERVAL = 15   # minutes
DELAY_BETWEEN_CALLS = 1.5  # seconds between Claude calls to avoid rate limits


def fetch_historical_ohlc(days=14):
    """Pull historical 1-minute candles from Kraken."""
    print(f"Fetching {days} days of historical BTC data from Kraken...")
    all_candles = []
    
    # Kraken OHLC returns max 720 candles per call
    # For 1-min candles, that's 12 hours per call
    # For 15-min candles, that's 7.5 days per call
    end_time = int(datetime.now(timezone.utc).timestamp())
    start_time = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    
    # Use 15-minute candles directly
    since = start_time
    while since < end_time:
        url = f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=15&since={since}"
        try:
            resp = requests.get(url, timeout=15)
            data = resp.json()
            if data.get("error") and len(data["error"]) > 0:
                print(f"  Kraken error: {data['error']}")
                break
            candles = data["result"].get("XXBTZUSD", [])
            if not candles:
                break
            all_candles.extend(candles)
            # Move forward
            last_ts = int(candles[-1][0])
            if last_ts <= since:
                break
            since = last_ts
            print(f"  Fetched {len(all_candles)} candles so far... (up to {datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')})")
            time.sleep(1)
        except Exception as e:
            print(f"  Error: {e}")
            time.sleep(3)
    
    if not all_candles:
        print("No data fetched!")
        return pd.DataFrame()
    
    # Convert to DataFrame
    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "vwap", "volume", "count"])
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
    for col in ["open", "high", "low", "close", "vwap", "volume"]:
        df[col] = df[col].astype(float)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    
    print(f"Total candles: {len(df)} | From {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")
    return df


def calculate_indicators_from_candles(df, idx):
    """Calculate indicators using candles up to index idx."""
    if idx < 30:
        return None
    
    window = df.iloc[max(0, idx-100):idx+1]
    prices = window["close"].values
    volumes = window["volume"].values
    current_price = float(prices[-1])
    
    # RSI
    rsi = None
    if len(prices) >= 15:
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-14:])
        avg_loss = np.mean(losses[-14:])
        if avg_loss > 0:
            rs = avg_gain / avg_loss
            rsi = round(100 - (100 / (1 + rs)), 2)
        else:
            rsi = 100.0
    
    # MACD
    macd = macd_sig = macd_hist = None
    if len(prices) >= 35:
        series = pd.Series(prices)
        ema12 = series.ewm(span=12, adjust=False).mean()
        ema26 = series.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd = round(float(macd_line.iloc[-1]), 4)
        macd_sig = round(float(signal_line.iloc[-1]), 4)
        macd_hist = round(float((macd_line - signal_line).iloc[-1]), 4)
    
    # Bollinger Bands
    bb_upper = bb_middle = bb_lower = bb_position = None
    if len(prices) >= 20:
        series = pd.Series(prices)
        bb_middle = float(series.rolling(20).mean().iloc[-1])
        std = float(series.rolling(20).std().iloc[-1])
        bb_upper = round(bb_middle + 2 * std, 2)
        bb_lower = round(bb_middle - 2 * std, 2)
        bb_middle = round(bb_middle, 2)
        if bb_upper != bb_lower:
            bb_position = round((current_price - bb_lower) / (bb_upper - bb_lower), 4)
    
    # EMAs
    ema_9 = round(float(pd.Series(prices).ewm(span=9, adjust=False).mean().iloc[-1]), 2) if len(prices) >= 9 else None
    ema_21 = round(float(pd.Series(prices).ewm(span=21, adjust=False).mean().iloc[-1]), 2) if len(prices) >= 21 else None
    sma_50 = round(float(np.mean(prices[-50:])), 2) if len(prices) >= 50 else None
    
    # Momentum
    momentum = round(float(prices[-1] - prices[-11]), 2) if len(prices) >= 11 else None
    
    # ROC
    roc = round(((prices[-1] - prices[-11]) / prices[-11]) * 100, 4) if len(prices) >= 11 else None
    
    # VWAP
    vwap = None
    valid_vol = volumes[volumes > 0]
    valid_prices = prices[volumes > 0]
    if len(valid_vol) > 0:
        vwap = round(float(np.sum(valid_prices * valid_vol) / np.sum(valid_vol)), 2)
    
    # EMA crossover
    ema_cross = None
    if ema_9 and ema_21:
        ema_cross = "BULLISH" if ema_9 > ema_21 else "BEARISH"
    
    # ATR
    atr = None
    if len(prices) >= 15:
        true_ranges = [abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]
        atr = round(float(np.mean(true_ranges[-14:])), 2)
    
    # OBV trend
    obv_trend = None
    if len(prices) >= 10:
        obv = [0]
        for i in range(1, len(prices)):
            v = volumes[i] if not np.isnan(volumes[i]) else 0
            if prices[i] > prices[i-1]:
                obv.append(obv[-1] + v)
            elif prices[i] < prices[i-1]:
                obv.append(obv[-1] - v)
            else:
                obv.append(obv[-1])
        if len(obv) >= 10:
            obv_trend = "RISING" if np.mean(obv[-5:]) > np.mean(obv[-10:-5]) else "FALLING"
    
    # Candle patterns (last 6 candles = 30 min of 5-min equivalent in 15-min terms)
    recent_candles = df.iloc[max(0, idx-5):idx+1]
    bodies = (recent_candles["close"] - recent_candles["open"]).values
    consecutive_green = 0
    consecutive_red = 0
    for b in reversed(bodies):
        if b > 0:
            consecutive_green += 1
        else:
            break
    for b in reversed(bodies):
        if b < 0:
            consecutive_red += 1
        else:
            break
    candle_trend = "BULLISH" if sum(bodies) > 0 else "BEARISH"
    
    # Volatility
    if len(recent_candles) >= 3:
        ranges = (recent_candles["high"] - recent_candles["low"]).values
        vol_expanding = float(ranges[-1]) > float(np.mean(ranges)) * 1.2
        vol_regime = "HIGH" if float(np.mean(ranges)) / current_price * 100 > 0.5 else "LOW" if float(np.mean(ranges)) / current_price * 100 < 0.15 else "NORMAL"
    else:
        vol_expanding = False
        vol_regime = "NORMAL"
    
    return {
        "current_price": current_price,
        "rsi": rsi,
        "macd": macd,
        "macd_signal": macd_sig,
        "macd_histogram": macd_hist,
        "bollinger_upper": bb_upper,
        "bollinger_middle": bb_middle,
        "bollinger_lower": bb_lower,
        "bollinger_position": bb_position,
        "ema_9": ema_9,
        "ema_21": ema_21,
        "sma_50": sma_50,
        "ema_crossover": ema_cross,
        "momentum": momentum,
        "rate_of_change": roc,
        "vwap": vwap,
        "price_vs_vwap": "ABOVE" if vwap and current_price > vwap else "BELOW" if vwap else None,
        "atr": atr,
        "obv_trend": obv_trend,
        "consecutive_green": consecutive_green,
        "consecutive_red": consecutive_red,
        "candle_trend_30m": candle_trend,
        "volatility_expanding": vol_expanding,
        "volatility_regime": vol_regime
    }


def ask_claude_backtest(indicators, past_results):
    """Simplified Claude call for backtesting."""
    past_text = ""
    if past_results:
        for r in past_results[-15:]:
            past_text += f"  {r['signal']} @ ${r['price']:,.2f} -> {r['outcome']} (moved ${r['change']:+,.2f})\n"
    
    prompt = f"""You are BTC Oracle backtesting. Predict UP or DOWN for the next 15 minutes.

Price: ${indicators['current_price']:,.2f}
RSI: {indicators.get('rsi', 'N/A')} | MACD: {indicators.get('macd', 'N/A')} (Hist: {indicators.get('macd_histogram', 'N/A')})
BB Position: {indicators.get('bollinger_position', 'N/A')} | EMA Cross: {indicators.get('ema_crossover', 'N/A')}
Momentum: {indicators.get('momentum', 'N/A')} | ROC: {indicators.get('rate_of_change', 'N/A')}%
VWAP: {indicators.get('price_vs_vwap', 'N/A')} | OBV: {indicators.get('obv_trend', 'N/A')} | ATR: {indicators.get('atr', 'N/A')}
Candle Trend: {indicators.get('candle_trend_30m', 'N/A')} | Green streak: {indicators.get('consecutive_green', 0)} | Red streak: {indicators.get('consecutive_red', 0)}
Volatility: {indicators.get('volatility_regime', 'N/A')}

Past signals (recent):
{past_text if past_text else '  No history yet.'}

JSON only: {{"signal": "UP" or "DOWN", "confidence": 0.0 to 1.0, "reasoning": "brief"}}"""

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)
    except Exception as e:
        return {"signal": "UP", "confidence": 0.5, "reasoning": f"Error: {e}"}


def run_backtest():
    """Main backtest loop."""
    print("=" * 60)
    print("BTC ORACLE - BACKTESTER")
    print("=" * 60)
    
    # Fetch data
    df = fetch_historical_ohlc(days=DAYS_BACK)
    if df.empty:
        print("No data to backtest!")
        return
    
    total_candles = len(df)
    print(f"\nBacktesting {total_candles} candles ({DAYS_BACK} days)")
    print(f"Each candle = {INTERVAL} minutes")
    print(f"Starting from candle 30 (need history for indicators)")
    print("=" * 60)
    
    results = []
    wins = 0
    losses = 0
    
    # Start from candle 30 to have enough indicator history
    # Step through every candle (each is 15 min)
    start_idx = 30
    end_idx = total_candles - 1  # need next candle for outcome
    
    test_count = 0
    max_tests = min(end_idx - start_idx, 1344)  # cap at 2 weeks worth
    
    for i in range(start_idx, end_idx):
        if test_count >= max_tests:
            break
        
        # Calculate indicators at this point
        indicators = calculate_indicators_from_candles(df, i)
        if not indicators:
            continue
        
        # Get Claude's prediction
        signal_data = ask_claude_backtest(indicators, results[-15:])
        
        # Check actual outcome
        current_price = float(df.iloc[i]["close"])
        next_price = float(df.iloc[i + 1]["close"])
        actual_went_up = next_price > current_price
        predicted_up = signal_data["signal"] == "UP"
        outcome = "WIN" if actual_went_up == predicted_up else "LOSS"
        change = next_price - current_price
        
        if outcome == "WIN":
            wins += 1
        else:
            losses += 1
        
        total = wins + losses
        win_rate = wins / total * 100 if total > 0 else 0
        
        result = {
            "timestamp": str(df.iloc[i]["timestamp"]),
            "price": current_price,
            "next_price": next_price,
            "signal": signal_data["signal"],
            "confidence": signal_data.get("confidence", 0.5),
            "outcome": outcome,
            "change": change,
            "reasoning": signal_data.get("reasoning", "")[:100]
        }
        results.append(result)
        test_count += 1
        
        # Print progress
        print(f"  [{test_count}/{max_tests}] {signal_data['signal']} @ ${current_price:,.2f} -> ${next_price:,.2f} ({'+' if change > 0 else ''}{change:.2f}) = {outcome} | WR: {win_rate:.1f}% ({wins}W/{losses}L)")
        
        # Rate limit
        time.sleep(DELAY_BETWEEN_CALLS)
        
        # Save checkpoint every 50 signals
        if test_count % 50 == 0:
            save_results(results, wins, losses)
    
    # Final results
    save_results(results, wins, losses)
    print_final_report(results, wins, losses)


def save_results(results, wins, losses):
    """Save backtest results to file."""
    total = wins + losses
    report = {
        "total_signals": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total * 100, 2) if total > 0 else 0,
        "results": results
    }
    with open("backtest_results.json", "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n  Checkpoint saved: {total} signals, {report['win_rate']}% win rate\n")


def print_final_report(results, wins, losses):
    """Print comprehensive backtest report."""
    total = wins + losses
    if total == 0:
        print("No results to report.")
        return
    
    win_rate = wins / total * 100
    
    print("\n" + "=" * 60)
    print("BACKTEST FINAL REPORT")
    print("=" * 60)
    print(f"Total Signals: {total}")
    print(f"Wins: {wins} | Losses: {losses}")
    print(f"Win Rate: {win_rate:.1f}%")
    
    # Analyze by confidence
    high_conf = [r for r in results if r["confidence"] >= 0.7]
    mid_conf = [r for r in results if 0.5 <= r["confidence"] < 0.7]
    low_conf = [r for r in results if r["confidence"] < 0.5]
    
    if high_conf:
        hc_wins = len([r for r in high_conf if r["outcome"] == "WIN"])
        print(f"\nHigh Confidence (>70%): {hc_wins}/{len(high_conf)} = {hc_wins/len(high_conf)*100:.1f}%")
    if mid_conf:
        mc_wins = len([r for r in mid_conf if r["outcome"] == "WIN"])
        print(f"Mid Confidence (50-70%): {mc_wins}/{len(mid_conf)} = {mc_wins/len(mid_conf)*100:.1f}%")
    if low_conf:
        lc_wins = len([r for r in low_conf if r["outcome"] == "WIN"])
        print(f"Low Confidence (<50%): {lc_wins}/{len(low_conf)} = {lc_wins/len(low_conf)*100:.1f}%")
    
    # Analyze by direction
    up_signals = [r for r in results if r["signal"] == "UP"]
    down_signals = [r for r in results if r["signal"] == "DOWN"]
    
    if up_signals:
        up_wins = len([r for r in up_signals if r["outcome"] == "WIN"])
        print(f"\nUP signals: {up_wins}/{len(up_signals)} = {up_wins/len(up_signals)*100:.1f}%")
    if down_signals:
        down_wins = len([r for r in down_signals if r["outcome"] == "WIN"])
        print(f"DOWN signals: {down_wins}/{len(down_signals)} = {down_wins/len(down_signals)*100:.1f}%")
    
    # Streaks
    max_win_streak = 0
    max_loss_streak = 0
    current_streak = 0
    for r in results:
        if r["outcome"] == "WIN":
            if current_streak > 0:
                current_streak += 1
            else:
                current_streak = 1
            max_win_streak = max(max_win_streak, current_streak)
        else:
            if current_streak < 0:
                current_streak -= 1
            else:
                current_streak = -1
            max_loss_streak = max(max_loss_streak, abs(current_streak))
    
    print(f"\nBest Win Streak: {max_win_streak}")
    print(f"Worst Loss Streak: {max_loss_streak}")
    
    # Average move
    changes = [abs(r["change"]) for r in results]
    print(f"\nAvg 15-min Move: ${np.mean(changes):.2f}")
    print(f"Max 15-min Move: ${max(changes):.2f}")
    
    # Win rate if we only traded high confidence
    if high_conf:
        print(f"\n>>> If we ONLY traded high confidence signals: {hc_wins}/{len(high_conf)} = {hc_wins/len(high_conf)*100:.1f}% win rate")
    
    print("=" * 60)
    print("Results saved to backtest_results.json")


if __name__ == "__main__":
    run_backtest()
