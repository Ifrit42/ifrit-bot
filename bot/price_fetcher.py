import requests

def get_last_price(pair: str) -> float:
    pair = pair.lower().replace("_", "")  

    url = f"https://indodax.com/api/ticker/{pair}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    # Guard against invalid pairs or unexpected shape
    if "error" in data:
        raise ValueError(f"Pair '{pair}' not supported: {data.get('error_description', '')}")

    if "ticker" not in data or "last" not in data["ticker"]:
        raise ValueError(f"No ticker data found for pair '{pair}'")

    return float(data["ticker"]["last"])