import requests
import json

PAIR_FILE = "pairs.json"
COINGECKO_URL = "https://api.coingecko.com/api/v3/exchanges/indodax/tickers"

def fetch_indodax_pairs():
    try:
        response = requests.get(COINGECKO_URL)
        response.raise_for_status()
        data = response.json()

        tickers = data.get("tickers", [])
        pairs = set()

        for ticker in tickers:
            base = ticker.get("base", "").lower()
            target = ticker.get("target", "").lower()
            if base and target:
                pair = f"{base}_{target}"
                pairs.add(pair)

        return sorted(pairs)
    except Exception as e:
        print(f"❌ Failed to fetch pairs: {e}")
        return []

def save_pairs(pairs):
    with open(PAIR_FILE, "w") as f:
        json.dump({"symbols": pairs}, f, indent=2)
    print(f"✅ Saved {len(pairs)} pairs to {PAIR_FILE}")

if __name__ == "__main__":
    pairs = fetch_indodax_pairs()
    if pairs:
        save_pairs(pairs)