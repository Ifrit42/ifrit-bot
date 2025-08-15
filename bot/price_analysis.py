import requests

def fetch_crypto_prices():
    url = "https://indodax.com/api/tickers"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json().get("tickers", {})

    return {
        "BTC": data.get("btc_idr", {}).get("last", "N/A"),
        "ETH": data.get("eth_idr", {}).get("last", "N/A"),
        "DOGE": data.get("doge_idr", {}).get("last", "N/A"),
        "SOL": data.get("sol_idr", {}).get("last", "N/A"),
    }