"""
BTC Oracle - News & Sentiment Scanner
Scans crypto news headlines and social sentiment for market-moving events.
"""

import requests
import time


def safe_get(url, timeout=10, headers=None):
    try:
        resp = requests.get(url, timeout=timeout, headers=headers or {})
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return None


def get_cryptopanic_news():
    """Get latest crypto news from CryptoPanic free API."""
    data = safe_get("https://cryptopanic.com/api/free/v1/posts/?auth_token=free&public=true&currencies=BTC&kind=news")
    if data and data.get("results"):
        headlines = []
        for post in data["results"][:10]:
            title = post.get("title", "")
            votes = post.get("votes", {})
            positive = votes.get("positive", 0)
            negative = votes.get("negative", 0)
            sentiment = "BULLISH" if positive > negative else "BEARISH" if negative > positive else "NEUTRAL"
            headlines.append({
                "title": title,
                "sentiment": sentiment,
                "source": post.get("source", {}).get("title", "Unknown"),
                "created_at": post.get("created_at", "")
            })
        return headlines
    return []


def get_coingecko_btc_sentiment():
    """Get BTC community sentiment from CoinGecko."""
    data = safe_get("https://api.coingecko.com/api/v3/coins/bitcoin")
    if data:
        sentiment = data.get("sentiment_votes_up_percentage", 50)
        community = data.get("community_data", {})
        return {
            "coingecko_sentiment_up_pct": sentiment,
            "coingecko_sentiment_down_pct": 100 - sentiment if sentiment else 50,
            "coingecko_sentiment_signal": "BULLISH" if sentiment and sentiment > 60 else "BEARISH" if sentiment and sentiment < 40 else "NEUTRAL",
            "reddit_subscribers": community.get("reddit_subscribers", 0),
            "reddit_active_accounts": community.get("reddit_accounts_active_48h", 0),
            "twitter_followers": community.get("twitter_followers", 0),
        }
    return {}


def get_reddit_sentiment():
    """Check r/bitcoin activity level."""
    try:
        headers = {"User-Agent": "BTC-Oracle/1.0"}
        resp = requests.get("https://www.reddit.com/r/bitcoin/hot.json?limit=10", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            posts = data.get("data", {}).get("children", [])
            if posts:
                total_score = sum(p["data"].get("score", 0) for p in posts)
                total_comments = sum(p["data"].get("num_comments", 0) for p in posts)
                titles = [p["data"].get("title", "") for p in posts[:5]]

                # Simple keyword sentiment
                bullish_words = ["bull", "moon", "pump", "buy", "ath", "breakout", "rally", "surge", "high"]
                bearish_words = ["bear", "crash", "dump", "sell", "drop", "fear", "low", "plunge", "correction"]

                bull_count = sum(1 for t in titles for w in bullish_words if w in t.lower())
                bear_count = sum(1 for t in titles for w in bearish_words if w in t.lower())

                return {
                    "reddit_avg_score": round(total_score / len(posts)),
                    "reddit_avg_comments": round(total_comments / len(posts)),
                    "reddit_activity": "HIGH" if total_comments > 500 else "NORMAL" if total_comments > 100 else "LOW",
                    "reddit_sentiment": "BULLISH" if bull_count > bear_count else "BEARISH" if bear_count > bull_count else "NEUTRAL",
                    "reddit_top_headlines": titles[:3]
                }
    except:
        pass
    return {}


def analyze_news_sentiment():
    """Aggregate all news and sentiment sources."""
    print("  Scanning news & sentiment...")
    result = {}

    # CoinGecko sentiment
    try:
        cg = get_coingecko_btc_sentiment()
        if cg:
            result.update(cg)
            print(f"    CoinGecko sentiment: {cg.get('coingecko_sentiment_signal', 'N/A')}")
    except:
        pass
    time.sleep(0.3)

    # Reddit
    try:
        reddit = get_reddit_sentiment()
        if reddit:
            headlines = reddit.pop("reddit_top_headlines", [])
            result.update(reddit)
            result["reddit_headlines"] = " | ".join(headlines) if headlines else "N/A"
            print(f"    Reddit: {reddit.get('reddit_sentiment', 'N/A')} (activity: {reddit.get('reddit_activity', 'N/A')})")
    except:
        pass
    time.sleep(0.3)

    # CryptoPanic news
    try:
        news = get_cryptopanic_news()
        if news:
            bullish = sum(1 for n in news if n["sentiment"] == "BULLISH")
            bearish = sum(1 for n in news if n["sentiment"] == "BEARISH")
            result["news_bullish_count"] = bullish
            result["news_bearish_count"] = bearish
            result["news_overall_sentiment"] = "BULLISH" if bullish > bearish else "BEARISH" if bearish > bullish else "NEUTRAL"
            result["news_headlines"] = " | ".join(n["title"] for n in news[:3])
            print(f"    News: {result['news_overall_sentiment']} ({bullish} bull / {bearish} bear)")
    except:
        pass

    # Overall sentiment score
    signals = []
    if result.get("coingecko_sentiment_signal"):
        signals.append(result["coingecko_sentiment_signal"])
    if result.get("reddit_sentiment"):
        signals.append(result["reddit_sentiment"])
    if result.get("news_overall_sentiment"):
        signals.append(result["news_overall_sentiment"])

    if signals:
        bull = signals.count("BULLISH")
        bear = signals.count("BEARISH")
        result["combined_sentiment"] = "STRONG_BULLISH" if bull >= 3 else "BULLISH" if bull > bear else \
                                       "STRONG_BEARISH" if bear >= 3 else "BEARISH" if bear > bull else "MIXED"

    return result


if __name__ == "__main__":
    data = analyze_news_sentiment()
    for k, v in data.items():
        print(f"  {k}: {v}")
