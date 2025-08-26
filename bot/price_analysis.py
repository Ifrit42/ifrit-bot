from indodax_api import IndodaxClient

client = IndodaxClient()

def fetch_price(pair: str):
    """Return last price for a trading pair, or None on error."""
    try:
        data = client.get_ticker(pair)
        return pair.upper(), float(data["ticker"]["last"])
    except Exception as e:
        print(f"Error fetching {pair}: {e}")
        return pair.upper(), None
