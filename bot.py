"""
BTC Oracle V3 - Full Arsenal + Deep Learning + News + Macro
"""

import os
import json
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import anthropic
import db
from indicators import get_all_indicators, fetch_recent_ticks
from market_data import get_all_market_data
from news_sentiment import get_all_sentiment_data
from pattern_analyzer import get_pattern_summary
from deep_analysis import quick_trade_review, deep_strategy_analysis, get_strategy_doc

load_dotenv()

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def get_past_signals(limit=20):
    return db.select("signals", f"order=created_at.desc&limit={limit}")


def get_journal_entries(limit=10):
    return db.select("journal", f"order=created_at.desc&limit={limit}")


def get_performance_stats():
    data = db.select("performance", "order=recorded_at.desc&limit=1")
    return data[0] if data else None


def ask_claude_for_signal(indicators, market_data, sentiment_data, pattern_summary, strategy_doc, past_signals, journal_entries, performance):
    past_text = ""
    if past_signals:
        for s in past_signals[:20]:
            outcome = s.get('outcome', 'PENDING') or 'PENDING'
            past_text += f"  {s['created_at']}: {s['signal']} @ ${s.get('btc_price_at_signal', 'N/A')} -> {outcome}\n"
    else:
        past_text = "  No previous signals yet.\n"

    journal_text = ""
    if journal_entries:
        for j in journal_entries[:15]:
            journal_text += f"  [{j['entry_type']}] {j['content'][:150]}\n"
    else:
        journal_text = "  No journal entries yet.\n"

    perf_text = "No performance data yet."
    if performance:
        perf_text = f"Win Rate: {performance.get('win_rate', 0):.1%} | Total: {performance.get('total_signals', 0)} | Wins: {performance.get('total_wins', 0)} | Losses: {performance.get('total_losses', 0)} | Streak: {performance.get('streak_current', 0)}"

    market_text = ""
    for k, v in market_data.items():
        market_text += f"  {k}: {v}\n"

    sentiment_text = ""
    for k, v in sentiment_data.items():
        sentiment_text += f"  {k}: {v}\n"

    strategy_section = ""
    if strategy_doc:
        strategy_section = f"""
=== YOUR STRATEGY DOCUMENT (follow these rules!) ===
{strategy_doc}
"""

    prompt = f"""You are BTC Oracle V3, an elite Bitcoin prediction AI with a massive data arsenal.

Predict whether BTC will be HIGHER or LOWER in exactly 15 minutes.

=== TECHNICAL INDICATORS ===
  Price: ${indicators['current_price']:,.2f}
  RSI (14): {indicators.get('rsi', 'N/A')}
  Stochastic RSI: K={indicators.get('stoch_rsi_k', 'N/A')} D={indicators.get('stoch_rsi_d', 'N/A')}
  MACD: {indicators.get('macd', 'N/A')} (Signal: {indicators.get('macd_signal', 'N/A')}, Hist: {indicators.get('macd_histogram', 'N/A')})
  Bollinger: Lower={indicators.get('bollinger_lower', 'N/A')} | Mid={indicators.get('bollinger_middle', 'N/A')} | Upper={indicators.get('bollinger_upper', 'N/A')}
  Bollinger Position: {indicators.get('bollinger_position', 'N/A')} (0=lower, 1=upper)
  EMA 9: {indicators.get('ema_9', 'N/A')} | EMA 21: {indicators.get('ema_21', 'N/A')} | SMA 50: {indicators.get('sma_50', 'N/A')}
  EMA Crossover: {indicators.get('ema_crossover', 'N/A')}
  Momentum: {indicators.get('momentum', 'N/A')} | ROC: {indicators.get('rate_of_change', 'N/A')}%
  VWAP: {indicators.get('vwap', 'N/A')} | Price vs VWAP: {indicators.get('price_vs_vwap', 'N/A')}
  ATR: {indicators.get('atr', 'N/A')} | OBV Trend: {indicators.get('obv_trend', 'N/A')}

=== ORDER FLOW & MARKET MICROSTRUCTURE ===
{market_text}

=== NEWS, SENTIMENT & MACRO ===
{sentiment_text}

=== YOUR PATTERN ANALYSIS (win/loss stats by condition) ===
{pattern_summary}
{strategy_section}
=== PAST SIGNALS ===
{past_text}

=== PERFORMANCE ===
{perf_text}

=== TRADE REVIEWS & JOURNAL ===
{journal_text}

=== DECISION FRAMEWORK (priority order) ===
1. FOLLOW YOUR STRATEGY DOCUMENT if one exists - it's based on your actual data
2. Order book imbalance + trade flow (most immediate signal)
3. News sentiment - any breaking news overrides technicals
4. Multi-timeframe momentum alignment
5. Liquidation pressure and volatility regime
6. Candlestick patterns + Bollinger position
7. Technical confluence (RSI + MACD + StochRSI + OBV + EMA crossover)
8. Market session context (Asia/Europe/US)
9. Macro correlations (gold, dollar strength)
10. Your pattern stats - avoid conditions where you historically lose

CRITICAL RULES:
- If 3+ major signals CONFLICT, lower confidence below 60%
- If order flow, momentum, AND news all ALIGN, confidence should be 80%+
- Check your strategy doc for specific rules you've developed
- After a loss streak of 3+, consider the OPPOSITE of your instinct
- Liquidation cascades create momentum - ride them, don't fade them

LEARNING MODE: Always output UP or DOWN. No WAIT.

Respond ONLY in this JSON format:
{{"signal": "UP" or "DOWN", "confidence": 0.0 to 1.0, "reasoning": "Your detailed analysis"}}"""

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)
    except Exception as e:
        print(f"Error calling Claude: {e}")
        return {"signal": "DOWN", "confidence": 0.5, "reasoning": f"Error: {e}"}


def log_signal(signal_data, indicators):
    record = {
        "signal": signal_data["signal"],
        "confidence": signal_data["confidence"],
        "btc_price_at_signal": indicators["current_price"],
        "rsi": indicators.get("rsi"),
        "macd": indicators.get("macd"),
        "macd_signal": indicators.get("macd_signal"),
        "macd_histogram": indicators.get("macd_histogram"),
        "bollinger_upper": indicators.get("bollinger_upper"),
        "bollinger_middle": indicators.get("bollinger_middle"),
        "bollinger_lower": indicators.get("bollinger_lower"),
        "volume_24h": indicators.get("volume_24h"),
        "momentum": indicators.get("momentum"),
        "ema_9": indicators.get("ema_9"),
        "ema_21": indicators.get("ema_21"),
        "sma_50": indicators.get("sma_50"),
        "vwap": indicators.get("vwap"),
        "analysis_notes": signal_data.get("reasoning", "")
    }
    result = db.insert("signals", record)
    print(f"  Signal logged: {signal_data['signal']} ({signal_data['confidence']:.0%} confidence)")
    return result


def check_previous_signals():
    cutoff_start = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    cutoff_end = (datetime.now(timezone.utc) - timedelta(minutes=14)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    signals = db.select("signals", f"outcome=is.null&created_at=gte.{cutoff_start}&created_at=lte.{cutoff_end}")

    if not signals:
        return

    recent = fetch_recent_ticks(minutes=5)
    if recent.empty:
        return
    current_price = float(recent["price"].iloc[-1])

    resolved_any = False
    for signal in signals:
        price_at = signal["btc_price_at_signal"]
        if not price_at:
            continue
        went_up = current_price > price_at
        predicted_up = signal["signal"] == "UP"
        outcome = "WIN" if went_up == predicted_up else "LOSS"

        db.update("signals", "id", signal["id"], {
            "btc_price_at_close": current_price,
            "outcome": outcome
        })
        change = current_price - price_at
        print(f"  Signal #{signal['id']}: {signal['signal']} -> {'UP' if went_up else 'DOWN'} (${change:+,.2f}) = {outcome}")
        resolved_any = True

    # Quick review after resolving trades
    if resolved_any:
        print("\n  Running quick trade review...")
        quick_trade_review()


def update_performance():
    data = db.select("signals", "outcome=not.is.null&select=outcome")
    if not data:
        return
    outcomes = [r["outcome"] for r in data]
    total = len(outcomes)
    wins = outcomes.count("WIN")
    losses = outcomes.count("LOSS")
    win_rate = wins / total if total > 0 else 0

    streak = 0
    for o in reversed(outcomes):
        if streak == 0:
            streak = 1 if o == "WIN" else -1
        elif (streak > 0 and o == "WIN") or (streak < 0 and o == "LOSS"):
            streak += 1 if streak > 0 else -1
        else:
            break

    db.insert("performance", {
        "total_signals": total, "total_wins": wins,
        "total_losses": losses, "win_rate": win_rate, "streak_current": streak
    })
    print(f"  Performance: {win_rate:.1%} ({wins}W/{losses}L) | Streak: {streak}")


def run_signal_cycle():
    print("\n" + "=" * 60)
    print(f"BTC ORACLE V3 | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60)

    print("\n[1/8] Checking previous signals + quick review...")
    check_previous_signals()

    print("\n[2/8] Updating performance...")
    update_performance()

    print("\n[3/8] Deep strategy analysis check...")
    deep_strategy_analysis()

    print("\n[4/8] Calculating technical indicators...")
    indicators = get_all_indicators()
    if not indicators:
        print("  Not enough data. Run collector.py first.")
        return

    print("\n[5/8] Fetching market microstructure data...")
    market_data = get_all_market_data()

    print("\n[6/8] Fetching news & sentiment...")
    sentiment_data = get_all_sentiment_data()

    print("\n[7/8] Analyzing patterns + loading strategy...")
    pattern_summary = get_pattern_summary()
    strategy_doc = get_strategy_doc()
    if strategy_doc:
        print(f"  Strategy doc loaded ({len(strategy_doc)} chars)")
    else:
        print("  No strategy doc yet (builds after 20 trades)")

    print("\n[8/8] Consulting Claude V3 with FULL ARSENAL...")
    signal_data = ask_claude_for_signal(
        indicators, market_data, sentiment_data, pattern_summary, strategy_doc,
        get_past_signals(20), get_journal_entries(15), get_performance_stats()
    )

    print(f"\n  >>> SIGNAL: {signal_data['signal']} ({signal_data['confidence']:.0%})")
    print(f"  >>> {signal_data.get('reasoning', '')[:200]}...")

    print("\n  Logging signal...")
    log_signal(signal_data, indicators)

    print("\nCycle complete.")


if __name__ == "__main__":
    run_signal_cycle()
