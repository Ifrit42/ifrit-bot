import requests

def fetch_trending_coins() -> list[dict]:
    url = "https://api.coingecko.com/api/v3/search/trending"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    items = resp.json().get("coins", [])
    coins = []
    for entry in items:
        coin = entry["item"]
        coin["coin_url"] = f"https://www.coingecko.com/en/coins/{coin['id']}"
        coins.append(coin)
    return coins