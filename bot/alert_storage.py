import json
import os
from pathlib import Path

PAIR_FILE  = "pairs.json"
ALERTS_FILE = Path("alerts.json")

# ──────────────────────────────────────────────────────────────────────────────
# Helper Functions

def get_pairs() -> list[str]:
    """Load trading pairs from pairs.json, normalize to UPPER_FULL_PAIR."""
    if not os.path.exists(PAIR_FILE):
        return []
    with open(PAIR_FILE, "r") as f:
        data = json.load(f)

    # If you used {"symbols": [...]}, pull from that; else, use top-level keys
    raw = data.get("symbols", data.keys() if isinstance(data, dict) else data)
    # Normalize and sort
    pairs = [p.upper() for p in raw]
    pairs.sort()
    return pairs


def load_alerts() -> dict:
    try:
        return json.loads(ALERTS_FILE.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}

def save_alerts(alerts: dict):
    ALERTS_FILE.write_text(json.dumps(alerts, indent=2))

