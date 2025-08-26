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
        """Fetch Indodax server time in seconds since epoch."""
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
        params["timestamp"] = self._get_server_time()

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
        return self._post("getInfo")

    def get_ticker(self, pair: str) -> dict:
        url = f"https://indodax.com/api/{pair}/ticker"
        response = requests.get(url)
        response.raise_for_status()
        return response.json()

    def get_ticker_v2(self, pair: str) -> dict:
        formatted_pair = pair.replace("_", "")
        url = f"https://indodax.com/api/ticker/{formatted_pair}"
        response = requests.get(url)
        response.raise_for_status()
        return response.json()

    def trade(self, pair, type_, price, amount):
        params = {
            "pair": pair,
            "type": type_,
            "price": price,
            "amount": amount
        }
        return self._post("trade", params)

    def get_trades(self, pair: str, limit: int = 100) -> list:
        url = f"https://indodax.com/api/{pair}/trades"
        resp = requests.get(url)
        resp.raise_for_status()
        return resp.json()[:limit]

    def get_balance(self, coin: str) -> float:
        info = self.get_account_info()
        balances = info["return"]["balance"]
        return float(balances.get(coin.lower(), 0))

    def create_buy_order(self, pair, price, amount):
        price = float(price)
        amount = float(amount)
        total_idr = price * amount

        if total_idr < 10000:
            raise ValueError(f"Minimum order 10,000 IDR — Your total: {total_idr}")

        params = {
            "pair": pair,
            "type": "buy",
            "price": price,
            "idr": total_idr  # use IDR instead of amount
        }
        return self._post("trade", params)

    def create_sell_order(self, pair, price, amount):
        params = {
            "pair": pair,
            "type": "sell",
            "price": float(price),
            "amount": float(amount)
        }
        return self._post("trade", params)

    def cancel_order(self, pair, order_id, type_):
        params = {
            "pair": pair,
            "order_id": order_id,
            "type": type_
        }
        return self._post("cancelOrder", params)

    def get_trade_history(self, pair: str, count: int = 10) -> list:
        """
        Fetch user's trade history for a given pair.
        Returns a list of trades or [] if none.
        """
        params = {
            "pair": pair,
            "count": count
        }

        data = self._post("tradeHistory", params)

        try:
            raw_trades = data.get("return", {}).get("trades", [])
            # Handle case: trades may be a dict keyed by trade_id
            if isinstance(raw_trades, dict):
                trades = list(raw_trades.values())
            elif isinstance(raw_trades, list):
                trades = raw_trades
            else:
                print(f"[DEBUG] Unexpected tradeHistory format: {data}")
                return []

            # Normalize keys so missing "amount" won’t break your code
            for t in trades:
                t.setdefault("amount", t.get("remain", "0"))

            return trades
        except Exception as e:
            print(f"[ERROR] Parsing trade history failed: {e}, raw={data}")
            return []
