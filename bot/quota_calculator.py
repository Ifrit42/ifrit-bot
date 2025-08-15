from indodax_api import IndodaxClient

def calculate_buy_quota(pair: str) -> float:
    """
    Returns how much of the base coin you can buy with your entire IDR balance.
    e.g. calculate_buy_quota('btc_idr')
    """
    client = IndodaxClient()
    ticker = client.get_ticker(pair)

    if not ticker or "ticker" not in ticker:
        raise RuntimeError(f"No ticker data for {pair}: {ticker}")

    data = ticker["ticker"]
    buy_str = data.get("buy")
    if buy_str is None:
        raise RuntimeError(f"Missing buy price for {pair}: {data}")

    try:
        buy_price = float(buy_str)
    except ValueError:
        raise RuntimeError(f"Could not parse buy price '{buy_str}' for {pair}")

    available_idr = client.get_balance("idr")
    if available_idr <= 0:
        raise RuntimeError("No IDR balance available to buy.")

    return available_idr / buy_price


def calculate_sell_quota(pair: str) -> float:
    """
    Returns how much IDR you would receive by selling your entire coin balance.
    """
    client = IndodaxClient()
    pair = pair.lower()
    symbol = pair.split("_")[0]

    # 1) Fetch ticker
    ticker = client.get_ticker(pair)
    if not ticker or "ticker" not in ticker:
        raise RuntimeError(f"No ticker data for {pair}: {ticker}")

    # 2) Parse sell price
    sell_str = ticker["ticker"].get("sell")
    if sell_str is None:
        raise RuntimeError(f"Missing sell price for {pair}: {ticker}")
    try:
        sell_price = float(sell_str)
    except ValueError:
        raise RuntimeError(f"Could not parse sell price '{sell_str}' for {pair}")
    if sell_price <= 0:
        raise RuntimeError(f"Non-positive sell price for {pair}: {sell_price}")

    # 3) Fetch your coin balance and compute IDR you’d get
    available_coin = client.get_balance(symbol)
    if available_coin <= 0:
        raise RuntimeError(f"No {symbol.upper()} balance available to sell.")

    return available_coin * sell_price



def count_market_activity(pair: str, limit: int = 10) -> tuple:
    """
    Returns (buy_count, sell_count) for the last `limit` trades of `pair`.
    """
    client = IndodaxClient()
    trades = client.get_trades(pair, limit)

    buy_count = sum(1 for t in trades if t.get("type", "").lower() == "buy")
    sell_count = sum(1 for t in trades if t.get("type", "").lower() == "sell")

    return buy_count, sell_count

def get_coin_balance(client, symbol: str) -> float:
    """
    Returns the available balance for `symbol` (e.g., 'btc', 'eth').
    """
    balance = client.get_balance(symbol)
    if balance is None:
        raise RuntimeError(f"Could not fetch balance for {symbol.upper()}")
    return float(balance)
