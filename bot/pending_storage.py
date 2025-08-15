import json
import os

PENDING_FILE = "pending_orders.json"

def load_pending_orders():
    if not os.path.exists(PENDING_FILE):
        return {}
    if os.path.getsize(PENDING_FILE) == 0:
        return {}
    with open(PENDING_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_pending_orders(data):
    with open(PENDING_FILE, "w") as f:
        json.dump(data, f, indent=4)

def add_pending_order(pair, order_data):
    data = load_pending_orders()
    if pair not in data:
        data[pair] = []
    data[pair].append(order_data)
    save_pending_orders(data)

def remove_pending_order_by_user(user_id, order_id):
    data = load_pending_orders()
    user_orders = data.get(str(user_id), [])
    data[str(user_id)] = [o for o in user_orders if str(o["order_id"]) != str(order_id)]
    save_pending_orders(data)
