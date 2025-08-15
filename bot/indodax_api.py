import os
import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv


class IndodaxClient:
    def __init__(self, api_key: str = None, api_secret: str = None):
        load_dotenv()

        if api_key and api_secret:
            self.key = api_key
            self.secret = api_secret.strip().encode()
        else:
            self.key = os.getenv("INDODAX_API_KEY")
            secret_env = os.getenv("INDODAX_SECRET_KEY")
            if not self.key or not secret_env:
                raise RuntimeError("Missing INDODAX_API_KEY or INDODAX_SECRET_KEY")
            self.secret = secret_env.strip().encode()

        self.api_url = "https://indodax.com/tapi"

    def _get_server_time(self):
        """
        Fetch Indodax server time in seconds since epoch.
        """
        try:
            resp = requests.get("https://indodax.com/api/server_time")
            resp.raise_for_status()
            data = resp.json()
            return int(data.get("server_time", time.time()))
        except Exception as e:
            print(f"[WARN] Could not fetch server time, using local time: {e}")
            return int(time.time())

    def _post(self, method, params=None):
        if params is None:
            params = {}

        params["method"] = method

        # Always use server time to avoid "Invalid timestamp"
        server_time = self._get_server_time()
        params["timestamp"] = server_time

        post_data = urlencode(params)
        sign = hmac.new(self.secret, post_data.encode(), hashlib.sha512).hexdigest()

        headers = {
            "Key": self.key,
            "Sign": sign
        }

        response = requests.post(self.api_url, data=params, headers=headers)
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError("Invalid JSON response from Indodax")

        if not data.get("success"):
            raise RuntimeError(data.get("error") or "Unknown TAPI error")

        return data

    def get_account_info(self):
        """Fetch your account details (balances, user info)."""
        return self._post("getInfo")

    def get_ticker(self, pair: str) -> dict:
        """Fetch ticker data for a trading pair, e.g. 'btc_idr'."""
        url = f"https://indodax.com/api/{pair}/ticker"
        response = requests.get(url)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            raise RuntimeError("Invalid JSON response from ticker API")

    def trade(self, pair, type_, price, amount):
        """Place a trade order on Indodax."""
        params = {
            "pair": pair,
            "type": type_,
            "price": price,
            "amount": amount
        }
        return self._post("trade", params)

    def get_trades(self, pair: str, limit: int = 100) -> list:
        """Fetch the most recent market trades for a given pair."""
        url = f"https://indodax.com/api/{pair}/trades"
        resp = requests.get(url)
        resp.raise_for_status()
        data = resp.json()
        return data[:limit]

    def get_balance(self, coin: str) -> float:
        """Return available balance for a given coin."""
        info = self.get_account_info()
        balances = info["return"]["balance"]
        return float(balances.get(coin.lower(), 0))

    def create_buy_order(self, pair, price, amount):
        price = float(price)
        amount = float(amount)
        total_idr = price * amount

        print(f"[DEBUG] Checking min order: Price={price}, Amount={amount}, Total={total_idr}")

        if total_idr < 10000:
            raise ValueError(f"Minimum order 10,000 IDR — Your total: {total_idr}")

        params = {
            "pair": pair,
            "type": "buy",
            "price": price,
            "idr": total_idr  # <-- send IDR instead of amount
        }
        return self._post("trade", params)

    def create_sell_order(self, pair, price, amount):
        """Create a sell order on Indodax."""
        params = {
            "pair": pair,
            "type": "sell",
            "price": price,
            "amount": amount
        }
        return self._post("trade", params)

    def cancel_order(self, pair, order_id, type_):
        """
        Cancel an open order on Indodax.
        :param pair: str - trading pair, e.g. 'doge_idr'
        :param order_id: int - order ID
        :param type_: str - 'buy' or 'sell'
        """
        params = {
            "pair": pair,
            "order_id": order_id,
            "type": type_
        }
        return self._post("cancelOrder", params)