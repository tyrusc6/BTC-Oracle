"""
BTC Oracle - Technical Indicators Calculator (FIXED)
Uses Kraken OHLC candles for accurate indicator calculation.
Uses tick data for granular real-time analysis.
"""

import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
import requests
import db


def fetch_recent_ticks(minutes=60):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    params = f"recorded_at=gte.{cutoff}&order=recorded_at.asc"
    data = db.select("tick_data", params)
    if data:
        df = pd.DataFrame(data)
        df["recorded_at"] = pd.to_datetime(df["recorded_at"])
        return df
    return pd.DataFrame()


def fetch_kraken_ohlc(interval=1, count=200):
    """Fetch OHLC candles directly from Kraken for accurate indicators."""
    try:
        url = f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval={interval}"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("result"):
            candles = data["result"].get("XXBTZUSD", [])
            if candles:
                df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "vwap", "volume", "count"])
                for col in ["open", "high", "low", "close", "vwap", "volume"]:
                    df[col] = df[col].astype(float)
                df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
                return df.tail(count)
    except Exception as e:
        print(f"  Error fetching Kraken OHLC: {e}")
    return pd.DataFrame()


def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    deltas = np.diff(prices)
    # Use Wilder's smoothing (exponential) for more accurate RSI
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal:
        return None, None, None
    series = pd.Series(prices)
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(histogram.iloc[-1])


def calculate_bollinger_bands(prices, period=20, std_dev=2):
    if len(prices) < period:
        return None, None, None
    series = pd.Series(prices)
    middle = series.rolling(window=period).mean().iloc[-1]
    std = series.rolling(window=period).std().iloc[-1]
    return float(middle + std_dev * std), float(middle), float(middle - std_dev * std)


def calculate_ema(prices, period):
    if len(prices) < period:
        return None
    return float(pd.Series(prices).ewm(span=period, adjust=False).mean().iloc[-1])


def calculate_sma(prices, period):
    if len(prices) < period:
        return None
    return float(np.mean(prices[-period:]))


def calculate_momentum(prices, period=10):
    if len(prices) < period + 1:
        return None
    return float(prices[-1] - prices[-period - 1])


def calculate_vwap(prices, volumes):
    if len(prices) == 0 or len(volumes) == 0:
        return None
    prices = np.array(prices, dtype=float)
    volumes = np.array(volumes, dtype=float)
    valid = ~np.isnan(volumes) & (volumes > 0)
    if not np.any(valid):
        return None
    return float(np.sum(prices[valid] * volumes[valid]) / np.sum(volumes[valid]))


def calculate_stoch_rsi(prices, period=14):
    if len(prices) < period * 2:
        return None, None
    rsi_values = []
    for i in range(period, len(prices)):
        rsi = calculate_rsi(prices[:i+1], period)
        if rsi is not None:
            rsi_values.append(rsi)
    if len(rsi_values) < period:
        return None, None
    series = pd.Series(rsi_values)
    lowest = series.rolling(period).min()
    highest = series.rolling(period).max()
    diff = highest - lowest
    diff = diff.replace(0, np.nan)
    stoch_rsi = (series - lowest) / diff
    k = stoch_rsi.rolling(3).mean()
    d = k.rolling(3).mean()
    k_val = float(k.iloc[-1]) * 100 if not pd.isna(k.iloc[-1]) else None
    d_val = float(d.iloc[-1]) * 100 if not pd.isna(d.iloc[-1]) else None
    return round(k_val, 2) if k_val else None, round(d_val, 2) if d_val else None


def calculate_atr(highs, lows, closes, period=14):
    """Proper ATR using high/low/close from candles."""
    if len(closes) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        true_ranges.append(tr)
    if len(true_ranges) < period:
        return None
    return float(np.mean(true_ranges[-period:]))


def calculate_obv_trend(prices, volumes):
    if len(prices) < 10 or len(volumes) < 10:
        return None
    obv = [0]
    for i in range(1, len(prices)):
        vol = volumes[i] if not np.isnan(volumes[i]) and volumes[i] > 0 else 0
        if prices[i] > prices[i-1]:
            obv.append(obv[-1] + vol)
        elif prices[i] < prices[i-1]:
            obv.append(obv[-1] - vol)
        else:
            obv.append(obv[-1])
    if len(obv) >= 10:
        return "RISING" if np.mean(obv[-5:]) > np.mean(obv[-10:-5]) else "FALLING"
    return None


def detect_trend(prices):
    """Detect the current price trend using multiple methods."""
    if len(prices) < 20:
        return "UNKNOWN", 0
    
    # Method 1: Price vs moving averages
    current = prices[-1]
    sma_10 = np.mean(prices[-10:])
    sma_20 = np.mean(prices[-20:])
    
    # Method 2: Linear regression slope
    x = np.arange(len(prices[-20:]))
    slope = np.polyfit(x, prices[-20:], 1)[0]
    
    # Method 3: Higher highs / lower lows
    recent_5 = prices[-5:]
    prev_5 = prices[-10:-5]
    higher_highs = max(recent_5) > max(prev_5)
    higher_lows = min(recent_5) > min(prev_5)
    
    # Method 4: Percentage change over last 30 candles
    pct_change = ((prices[-1] - prices[-min(30, len(prices))]) / prices[-min(30, len(prices))]) * 100
    
    # Score the trend
    trend_score = 0
    if current > sma_10:
        trend_score += 1
    else:
        trend_score -= 1
    if current > sma_20:
        trend_score += 1
    else:
        trend_score -= 1
    if sma_10 > sma_20:
        trend_score += 1
    else:
        trend_score -= 1
    if slope > 0:
        trend_score += 1
    else:
        trend_score -= 1
    if higher_highs and higher_lows:
        trend_score += 1
    elif not higher_highs and not higher_lows:
        trend_score -= 1
    
    if trend_score >= 3:
        trend = "STRONG_UPTREND"
    elif trend_score >= 1:
        trend = "UPTREND"
    elif trend_score <= -3:
        trend = "STRONG_DOWNTREND"
    elif trend_score <= -1:
        trend = "DOWNTREND"
    else:
        trend = "SIDEWAYS"
    
    return trend, round(pct_change, 4)


def get_all_indicators():
    print("Calculating indicators...")
    
    # Use Kraken OHLC candles for accurate indicators (1-min candles)
    df_1m = fetch_kraken_ohlc(interval=1, count=200)
    df_5m = fetch_kraken_ohlc(interval=5, count=100)
    
    if df_1m.empty or len(df_1m) < 30:
        print("  Not enough OHLC data from Kraken")
        # Fallback to tick data
        tick_df = fetch_recent_ticks(minutes=120)
        if tick_df.empty or len(tick_df) < 30:
            print(f"  Not enough tick data either ({len(tick_df) if not tick_df.empty else 0} ticks)")
            return None
        # Resample ticks to 1-min candles
        tick_df = tick_df.set_index("recorded_at")
        df_1m = tick_df["price"].resample("1min").ohlc().dropna()
        df_1m.columns = ["open", "high", "low", "close"]
        df_1m["volume"] = tick_df["volume"].resample("1min").sum().fillna(0)
        df_1m = df_1m.reset_index()
    
    prices = df_1m["close"].values.astype(float)
    highs = df_1m["high"].values.astype(float)
    lows = df_1m["low"].values.astype(float)
    volumes = df_1m["volume"].values.astype(float)
    current_price = float(prices[-1])
    
    # Calculate all indicators from proper candle data
    rsi = calculate_rsi(prices)
    macd, macd_sig, macd_hist = calculate_macd(prices)
    bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(prices)
    stoch_k, stoch_d = calculate_stoch_rsi(prices)
    atr = calculate_atr(highs, lows, prices)
    obv_trend = calculate_obv_trend(prices, volumes)
    roc = round(((prices[-1] - prices[-11]) / prices[-11]) * 100, 4) if len(prices) >= 11 else None
    vwap = calculate_vwap(prices, volumes)
    
    # Trend detection
    trend, trend_pct = detect_trend(prices)
    
    # Also detect trend on 5-min candles for higher timeframe
    trend_5m = "UNKNOWN"
    if not df_5m.empty and len(df_5m) >= 20:
        prices_5m = df_5m["close"].values.astype(float)
        trend_5m, _ = detect_trend(prices_5m)
    
    # Bollinger Band position
    bb_position = None
    if bb_upper and bb_lower and bb_upper != bb_lower:
        bb_position = round((current_price - bb_lower) / (bb_upper - bb_lower), 4)
    
    ema_9 = calculate_ema(prices, 9)
    ema_21 = calculate_ema(prices, 21)
    sma_50 = calculate_sma(prices, 50)
    momentum = calculate_momentum(prices)
    
    indicators = {
        "current_price": current_price,
        "rsi": round(rsi, 2) if rsi is not None else None,
        "macd": round(macd, 4) if macd is not None else None,
        "macd_signal": round(macd_sig, 4) if macd_sig is not None else None,
        "macd_histogram": round(macd_hist, 4) if macd_hist is not None else None,
        "bollinger_upper": round(bb_upper, 2) if bb_upper else None,
        "bollinger_middle": round(bb_middle, 2) if bb_middle else None,
        "bollinger_lower": round(bb_lower, 2) if bb_lower else None,
        "bollinger_position": bb_position,
        "ema_9": round(ema_9, 2) if ema_9 else None,
        "ema_21": round(ema_21, 2) if ema_21 else None,
        "sma_50": round(sma_50, 2) if sma_50 else None,
        "ema_crossover": "BULLISH" if ema_9 and ema_21 and ema_9 > ema_21 else "BEARISH" if ema_9 and ema_21 else None,
        "momentum": round(momentum, 2) if momentum is not None else None,
        "rate_of_change": roc,
        "vwap": round(vwap, 2) if vwap else None,
        "price_vs_vwap": "ABOVE" if vwap and current_price > vwap else "BELOW" if vwap else None,
        "stoch_rsi_k": stoch_k,
        "stoch_rsi_d": stoch_d,
        "atr": round(atr, 2) if atr else None,
        "obv_trend": obv_trend,
        "trend_1m": trend,
        "trend_5m": trend_5m,
        "trend_pct_change": trend_pct,
        "volume_24h": float(volumes[-1]) if len(volumes) > 0 else None,
        "tick_count": len(df_1m),
        "data_span_minutes": len(df_1m)
    }
    
    print(f"  Price: ${current_price:,.2f} | RSI: {indicators['rsi']} | Trend 1m: {trend} | Trend 5m: {trend_5m} | OBV: {obv_trend}")
    return indicators


if __name__ == "__main__":
    result = get_all_indicators()
    if result:
        print("Indicators calculated!")
        for k, v in result.items():
            print(f"  {k}: {v}")
    else:
        print("Need more data.")
