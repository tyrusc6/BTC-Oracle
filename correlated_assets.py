"""
BTC Oracle - Correlated Assets Tracker
Monitors gold, S&P 500 futures, DXY, and other assets that influence BTC.
"""

import requests
import time


def safe_get(url, timeout=10):
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return None


def get_gold_price():
    """Gold price from metals API."""
    # Use CoinGecko's PAXG (gold-backed token) as gold proxy
    data = safe_get("https://api.coingecko.com/api/v3/simple/price?ids=pax-gold&vs_currencies=usd&include_24hr_change=true")
    if data and data.get("pax-gold"):
        gold = data["pax-gold"]
        return {
            "gold_price": gold.get("usd", 0),
            "gold_24h_change_pct": round(gold.get("usd_24h_change", 0), 2),
            "gold_btc_correlation": "POSITIVE" if gold.get("usd_24h_change", 0) > 0 else "NEGATIVE"
        }
    return {}


def get_major_crypto_momentum():
    """Track top cryptos to gauge overall market momentum."""
    data = safe_get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,binancecoin,ripple,cardano&vs_currencies=usd&include_24hr_change=true&include_1hr_change=true")
    if data:
        result = {}
        gainers = 0
        losers = 0
        for coin, prices in data.items():
            change_1h = prices.get("usd_1h_change", 0) or 0
            change_24h = prices.get("usd_24h_change", 0) or 0
            if change_1h > 0:
                gainers += 1
            else:
                losers += 1
            result[f"{coin}_1h_change"] = round(change_1h, 2)
            result[f"{coin}_24h_change"] = round(change_24h, 2)

        total = gainers + losers
        result["crypto_market_breadth"] = round(gainers / total * 100, 1) if total > 0 else 50
        result["crypto_market_signal"] = "RISK_ON" if gainers > losers else "RISK_OFF" if losers > gainers else "NEUTRAL"
        result["crypto_gainers"] = gainers
        result["crypto_losers"] = losers

        # ETH/BTC ratio trend
        if data.get("bitcoin", {}).get("usd") and data.get("ethereum", {}).get("usd"):
            eth_btc = data["ethereum"]["usd"] / data["bitcoin"]["usd"]
            result["eth_btc_ratio"] = round(eth_btc, 6)

        return result
    return {}


def get_stablecoin_flows():
    """Monitor stablecoin market cap changes - money flowing in/out of crypto."""
    data = safe_get("https://api.coingecko.com/api/v3/simple/price?ids=tether,usd-coin,dai&vs_currencies=usd&include_market_cap=true&include_24hr_change=true")
    if data:
        total_mcap = 0
        for coin in ["tether", "usd-coin", "dai"]:
            if data.get(coin, {}).get("usd_market_cap"):
                total_mcap += data[coin]["usd_market_cap"]

        tether_change = data.get("tether", {}).get("usd_24h_change", 0) or 0
        return {
            "stablecoin_total_mcap_b": round(total_mcap / 1e9, 2),
            "stablecoin_flow_signal": "INFLOW" if tether_change > 0.01 else "OUTFLOW" if tether_change < -0.01 else "STABLE"
        }
    return {}


def get_btc_specific_metrics():
    """BTC-specific on-chain-ish metrics from CoinGecko."""
    data = safe_get("https://api.coingecko.com/api/v3/coins/bitcoin?localization=false&tickers=false&community_data=false&developer_data=false")
    if data and data.get("market_data"):
        md = data["market_data"]
        ath = md.get("ath", {}).get("usd", 0)
        current = md.get("current_price", {}).get("usd", 0)
        ath_pct = ((current - ath) / ath * 100) if ath else 0

        return {
            "btc_ath": ath,
            "btc_ath_distance_pct": round(ath_pct, 2),
            "btc_market_cap_rank": data.get("market_cap_rank", 1),
            "btc_price_change_1h": round(md.get("price_change_percentage_1h_in_currency", {}).get("usd", 0) or 0, 3),
            "btc_price_change_24h": round(md.get("price_change_percentage_24h", 0) or 0, 2),
            "btc_price_change_7d": round(md.get("price_change_percentage_7d", 0) or 0, 2),
            "btc_price_change_14d": round(md.get("price_change_percentage_14d", 0) or 0, 2),
            "btc_price_change_30d": round(md.get("price_change_percentage_30d", 0) or 0, 2),
            "btc_high_24h": md.get("high_24h", {}).get("usd", 0),
            "btc_low_24h": md.get("low_24h", {}).get("usd", 0),
            "btc_circulating_supply": md.get("circulating_supply", 0),
            "btc_total_volume": md.get("total_volume", {}).get("usd", 0),
        }
    return {}


def get_all_correlated_data():
    """Fetch all correlated asset data."""
    print("  Fetching correlated assets...")
    all_data = {}

    sources = [
        ("Gold", get_gold_price),
        ("Crypto Momentum", get_major_crypto_momentum),
        ("Stablecoin Flows", get_stablecoin_flows),
        ("BTC Metrics", get_btc_specific_metrics),
    ]

    for name, func in sources:
        try:
            data = func()
            if data:
                all_data.update(data)
                print(f"    {name}: OK")
            else:
                print(f"    {name}: No data")
        except Exception as e:
            print(f"    {name}: Error - {e}")
        time.sleep(1.5)  # CoinGecko rate limit

    return all_data


if __name__ == "__main__":
    data = get_all_correlated_data()
    for k, v in data.items():
        print(f"  {k}: {v}")
