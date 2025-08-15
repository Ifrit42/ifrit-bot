from dotenv import load_dotenv
from news_fetcher import fetch_crypto_news
from price_analysis import fetch_crypto_prices
from quota_calculator import (
    calculate_buy_quota,
    calculate_sell_quota,
    count_market_activity
)
from indodax_api import IndodaxClient
from prettytable import PrettyTable
import os
import time

load_dotenv()


def print_market_activity(pair, client, limit=100):
    trades = client.get_trades(pair, limit)
    
    # Build and print the main table
    table = PrettyTable(["Time", "Type", "Price (IDR)", pair.split("_")[0].upper()])
    for t in trades:
        ts = int(float(t["date"]))
        time_str = time.strftime("%d-%b-%y %H:%M", time.localtime(ts))
        table.add_row([
            time_str,
            t["type"].capitalize(),
            f"{float(t['price']):,.0f}",
            f"{float(t['amount']):.6f}"
        ])
    print(f"\n📊 Market Activity for {pair.upper()}")
    print(table)

    # Compute buys and sells
    buy_count  = sum(1 for t in trades if t["type"] == "buy")
    sell_count = sum(1 for t in trades if t["type"] == "sell")

    # Print a dynamic summary line
    coin_sym   = pair.split("_")[0].upper()
    print(
        f"In the last {limit} trades for {coin_sym}_IDR: "
        f"{buy_count} Buys, {sell_count} Sells\n"
    )



def main():
    # 1) News & Prices
    client = IndodaxClient()
    coin   = input("Enter coin symbol (e.g. btc, eth, doge, or others): ").strip().lower()
    pair   = f"{coin}_idr"

    trade_limit = int(input("How many of the last trades to show?: ") or 10)
    print_market_activity(pair, client, limit=trade_limit)

    print("\n📈 Fetching latest crypto news...")
    for i, title in enumerate(fetch_crypto_news(), start=1):
        print(f"\n📰 News {i}: {title}")

    print("\n💰 Fetching top coin prices...")
    for symbol, price in fetch_crypto_prices().items():
        print(f"{symbol}: Rp {price}")

    print(
    f"In the last 1 - {trade_limit} trades for {coin}_IDR: "
    f"{calculate_buy_quota} Buys, {calculate_sell_quota} Sells\n"
    )

    # print_market_activity(pair, client, limit=10)
    pairs = ["btc_idr", "doge_idr", "eth_idr","alif_idr"]
    for pair in pairs:
        try:
            quota = calculate_buy_quota(pair)
            print(f"You can buy {quota:.6f} {pair.split('_')[0].upper()}")
        except Exception as e:
            print(f"Error for {pair}: {e}")

    pair = os.getenv("COIN_PAIR", "eth_idr")
    client = IndodaxClient()


if __name__ == "__main__":
    main()