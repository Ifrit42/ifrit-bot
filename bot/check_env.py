
from indodax_api import IndodaxClient

client = IndodaxClient()
ticker = client.get_ticker("btc_idr")
print(ticker)
