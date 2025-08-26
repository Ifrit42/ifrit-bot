import os
import json
import re
import time
import discord
from discord.ext import commands
from discord import Embed
from discord.ext import tasks
from indodax_api import IndodaxClient
from prettytable import PrettyTable
from dotenv import load_dotenv
import numpy as np
from functools import wraps
import asyncio
import requests
import aiohttp
import math


from alert_storage import load_alerts, save_alerts, get_pairs
from pending_storage import load_pending_orders, save_pending_orders, add_pending_order, remove_pending_order_by_user
from price_fetcher import get_last_price
from indodax_api      import IndodaxClient
from news_fetcher   import fetch_crypto_news
from paginator      import NewsPaginator, PairsPaginator, PricesPaginator
from coingecko import fetch_trending_coins
from price_analysis import fetch_price
from quota_calculator import (
    calculate_buy_quota,
    calculate_sell_quota,
    count_market_activity,
    get_coin_balance
)

CRED_FILE = "user_credentials.json"

MAINTENANCE_MODE = False  # Change to False to disable
BOT_OWNERS = [527832667845033994, 1402691770545995796,577029761910439962]

with open("pairs.json") as f:
    PAIRS = json.load(f)["symbols"]

def find_pair(symbol: str) -> str:
    """Convert short coin name into Indodax full pair name."""
    symbol = symbol.lower()
    for p in PAIRS:
        if p.startswith(symbol + "_"):
            return p
    return None

def is_owner():
    async def predicate(ctx):
        if ctx.author.id not in BOT_OWNERS:
            await ctx.send(embed=discord.Embed(
                title="⛔ Access Denied",
                description="You are not authorized to use this command.",
                color=discord.Color.red()
            ))
            return False
        return True
    return commands.check(predicate)

def maintenance_check():
    async def predicate(ctx):
        # Allow owners to bypass maintenance
        if ctx.author.id in BOT_OWNERS:
            return True

        if MAINTENANCE_MODE:
            await ctx.send(embed=discord.Embed(
                title="🛠 Bot Under Maintenance",
                description="Please try again later.",
                color=discord.Color.orange()
            ))
            return False
        return True
    return commands.check(predicate)

def load_credentials():
    if not os.path.isfile(CRED_FILE):
        return {}
    with open(CRED_FILE, "r") as f:
        return json.load(f)

def save_credentials(creds):
    with open(CRED_FILE, "w") as f:
        json.dump(creds, f, indent=2)

def with_typing(func):
    @wraps(func)
    async def wrapped(ctx, *args, **kwargs):
        async with ctx.typing():
            # Optional: short artificial delay to make it look more natural
            await asyncio.sleep(1.5)
            return await func(ctx, *args, **kwargs)
    return wrapped

# Stoploss background task
async def stoploss_monitor():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            with open("pending_orders.json", "r") as f:
                data = json.load(f)

            if "stoploss" in data:
                for sl in data["stoploss"]:
                    if not sl.get("active"):
                        continue

                    current_price = get_last_price(sl["pair"])
                    if current_price <= sl["stop_price"]:
                        # Notify user
                        user = bot.get_user(sl["user"])
                        if user:
                            await user.send(
                                f"🛑 STOPLOSS TRIGGERED for {sl['coin']}!\n"
                                f"Price dropped to {current_price:,.0f} IDR (Stop: {sl['stop_price']:,.0f})"
                            )
                        sl["active"] = False  # deactivate after triggering

            # Save updates
            with open("pending_orders.json", "w") as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            print(f"[Stoploss Monitor Error] {e}")

        await asyncio.sleep(30)  # check every 30s

    bot.loop.create_task(stoploss_monitor())


# Monitor Alerts
# This background task checks for alerts every 15 seconds

async def monitor_alerts():
    await bot.wait_until_ready()
    while not bot.is_closed():
        # Offload blocking file read
        alerts = await asyncio.to_thread(load_alerts)

        # Iterate over a shallow copy so we can remove items safely
        for uid, user_alerts in list(alerts.items()):
            for alert in list(user_alerts):
                pair = alert["pair"]
                target = alert["target"]

                try:
                    # Offload blocking HTTP/CPU work if get_last_price is sync
                    price = await asyncio.to_thread(get_last_price, pair)
                except ValueError as ve:
                    print(f"[Monitor] Skipping {pair}: {ve}")
                    user_alerts.remove(alert)
                    continue
                except Exception as e:
                    print(f"[Monitor] Error fetching {pair}: {e}")
                    continue

                # Trigger alert
                if price >= target:
                    try:
                        user = await bot.fetch_user(int(uid))
                        await user.send(f"🚨 `{pair}` hit `{price}` IDR (target `{target}`)")
                    except Exception as e:
                        # Don't let a DM failure block the loop
                        print(f"[Monitor] DM failed for {uid}: {e}")
                    finally:
                        user_alerts.remove(alert)

        # Persist any removals or changes (offload blocking file write)
        await asyncio.to_thread(save_alerts, alerts)

        # Yield control so Discord heartbeats can run
        await asyncio.sleep(15)

@tasks.loop(seconds=15)
async def check_pending_orders():
    client = IndodaxClient()
    data = load_pending_orders()
    changed = False

    for user_id, orders in data.items():
        for order in orders:
            if order["status"] == "pending":
                trades = client.get_trade_history(order["pair"], count=20)

                # Check if order_id appears in trade history
                for t in trades:
                    if str(t.get("order_id")) == str(order["order_id"]):
                        if float(t.get("remain", 0)) == 0:  # fully filled
                            order["status"] = "completed"
                            changed = True

                            user = await bot.fetch_user(int(user_id))
                            await user.send(
                                f"✅ Your buy order {order['order_id']} for {order['amount']} {order['pair']} has been filled!"
                            )

    if changed:
        save_pending_orders(data)

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")
dex_client = IndodaxClient()

@bot.check
async def global_maintenance_check(ctx):
    # Allow bot owners to bypass
    if ctx.author.id in BOT_OWNERS:
        return True

    if MAINTENANCE_MODE:
        await ctx.send(embed=discord.Embed(
            title="🛠 Bot Under Maintenance",
            description="Please try again later.",
            color=discord.Color.orange()
        ))
        return False
    return True

@bot.event
@with_typing
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        # Create usage guide for specific commands
        if ctx.command.name == "alert":
            usage_msg = "❌ Usage: `!alert <symbol> <price>`\nExample: `!alert doge 10000` or `!alert doge +10%`"
        elif ctx.command.name == "maintenance_on":
            usage_msg = "❌ Usage: `!maintenance_on` (Admin only)"
        elif ctx.command.name == "maintenance_off":
            usage_msg = "❌ Usage: `!maintenance_off` (Admin only)"
        elif ctx.command.name == "setkeys":
            usage_msg = "❌ Usage: Type `!setkeys YOUR_API_KEY YOUR_API_SECRET` in a DM to the bot."
        elif ctx.command.name == "alert_list":
            usage_msg = "❌ Usage: `!alert_list` to see your active alerts."
        elif ctx.command.name == "remove_alert":
            usage_msg = "❌ Usage: `!remove_alert <index>` to remove an alert by its index."
        elif ctx.command.name == "crypto_news":
            usage_msg = "❌ Usage: `!crypto_news [limit]` to fetch latest news. Default limit is 10."
        elif ctx.command.name == "market":
            usage_msg = "❌ Usage: `!market <coin> [limit]` to fetch trades. Default limit is 100."
        elif ctx.command.name == "crypto_prices":
            usage_msg = "❌ Usage: `!crypto_prices` to fetch current top coin prices."
        elif ctx.command.name == "analyze":
            usage_msg = "❌ Usage: `!analyze <coin> <time>(1h by default)` to analyze when to buy/sell based on news & market stats."
        elif ctx.command.name == "trending":
            usage_msg = "❌ Usage: `!trending` to show the current top trending cryptocurrencies."
        elif ctx.command.name == "balance":
            usage_msg = "❌ Usage: `!balance` to check your Indodax account balance."
        elif ctx.command.name == "pairs":
            usage_msg = "❌ Usage: `!pairs` to list all trading pairs."
        elif ctx.command.name == "buy":
            usage_msg = "❌ Usage: `!buy <symbol> <price> <amount>`" \
            "\nto place a buy order.\nExample: `!buy doge 3685 5`" \
            "\n Minimum Buy Price : **10.000 IDR**"
        elif ctx.command.name == "sell":
            usage_msg = "❌ Usage: `!sell <symbol> <price> <amount>`" \
            "\nto place a sell order.\nExample: `!sell doge 4000 10`" \
            "\n Minimum Sell Price : **25.000 IDR**"
        elif ctx.command.name == "buy_list":
            usage_msg = "❌ Usage: `!buy_list` to list all your active/pending buy orders."
        elif ctx.command.name == "cancelbuy":
            usage_msg = "❌ Usage: `!cancelbuy <order_id>` to cancel a specific buy order.\nExample: `!cancelbuy 123456`"
        elif ctx.command.name == "sell_list":
            usage_msg = "❌ Usage: `!sell_list` to list all your active/pending sell orders."
        elif ctx.command.name == "cancelsell":
            usage_msg = "❌ Usage: `!cancelsell <order_id>` to cancel a specific sell order.\nExample: `!cancelsell 987654`"
        elif ctx.command.name == "maintenance":
            usage_msg = "❌ Usage: `!maintenance on/off` to toggle maintenance mode (Admin only)."
        elif ctx.command.name == "help":
            usage_msg = "❌ Usage: `!help` to see the list of available commands."
        elif ctx.command.name == "ping":
            usage_msg = "❌ Usage: `!ping` to check bot responsiveness."
        elif ctx.command.name == "trade_history":
            usage_msg = "❌ Usage: `!trade_history <pair> [count]` to fetch your trade history for a specific pair."
        else:
            usage_msg = f"❌ Missing argument(s) for `{ctx.command}`"

        embed = discord.Embed(
            title="Command Usage Error",
            description=usage_msg,
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

    elif isinstance(error, commands.CommandNotFound):
        # Unknown command
        await ctx.send("❓ Unknown command. Type `!help` to see available commands.")

    else:
        # Re-raise unexpected errors so you can see them in console
        raise error

@bot.event
async def on_ready():
    bot.loop.create_task(monitor_alerts())

    # Choose one of these Activity types:
    # activity = discord.Game(name="with crypto signals")
    # activity = discord.Activity(type=discord.ActivityType.watching, name="The Market")
    activity = discord.Activity(type=discord.ActivityType.listening, name="!help · ifrit-ai")
    # activity = discord.Activity(type=discord.ActivityType.competing, name="for your attention")

    await bot.change_presence(
        status=discord.Status.online,   # online, idle, dnd, invisible
        activity=activity
    )
    print(f"✅ Logged in as {bot.user}")

@bot.event
async def on_guild_join(guild):
    try:
        # Try to find the owner or a member with permission to manage the server
        if guild.owner:
            target_user = guild.owner
        else:
            target_user = None
            for member in guild.members:
                if member.guild_permissions.administrator:
                    target_user = member
                    break
        
        if target_user:
            await target_user.send(
                f"👋 Thanks for adding **{bot.user.name}** to **{guild.name}**!\n\n"
                "📖 Please read the full setup guide and command list here :\n"
                "https://github.com/Ifrit42/ifrit-bot/blob/main/%F0%9F%97%92README\n\n"
                "If you have any questions, feel free to ask in the support server!"
            )
    except Exception as e:
        print(f"Could not send welcome DM: {e}")

@bot.command(name="maintenance")
@is_owner()
async def maintenance_toggle(ctx, mode: str):
    global MAINTENANCE_MODE

    # Only bot owners can run this
    if ctx.author.id not in BOT_OWNERS:
        return await ctx.send("❌ You don’t have permission to do that.")

    if mode.lower() == "on":
        MAINTENANCE_MODE = True
        await ctx.send(embed=discord.Embed(
            title="🛠 Maintenance Mode Enabled",
            description="The bot is now under maintenance. All commands are disabled for regular users.",
            color=discord.Color.orange()
        ))

    elif mode.lower() == "off":
        MAINTENANCE_MODE = False
        await ctx.send(embed=discord.Embed(
            title="✅ Maintenance Mode Disabled",
            description="The bot is now active and ready to use.",
            color=discord.Color.green()
        ))

    else:
        await ctx.send("❌ Invalid mode. Use `!maintenance on` or `!maintenance off`.")


@bot.command(name="help")
@maintenance_check()
@with_typing
async def help_command(ctx):
    """
    Sends an embed with all available commands, grouped by category.
    """
    from discord import Embed

    command_categories = {
        "General": [
            ("ping", "Ping the bot to check responsiveness.", "`!ping`", None),
            ("help", "Display this help message.", "`!help`", None),
            ("trending", "Show the current top trending cryptocurrencies.", "`!trending`", None),
            ("analyze", "Analyze when to buy/sell based on news & market stats.", "`!analyze <coin> <time>(1h default)`", "`!analyze floki`"),
            ("market", "Show recent market trades for a coin.", "`!market <coin> [limit]`", "`!market btc 500`"),
            ("crypto_prices", "Fetch current top coin prices.", "`!crypto_prices`", None),
            ("crypto_news", "Browse the latest crypto news with pagination.", "`!crypto_news [limit]`", "`!crypto_news 10`"),
            ("price", "Get the current price of a cryptocurrency.", "`!price <coin>`", "`!price btc`"),
        ],
        "Alerts": [
            ("alert", "Set a price or percentage alert for a coin.", "`!alert <coin> <price|%>`", "`!alert doge 10000` or `!alert eth +10%`"),
            ("removealert", "Remove an existing price alert for a coin.", "`!removealert <coin>`", "`!removealert btc`"),
            ("alerts", "View all your active alerts.", "`!alerts`", None),
        ],
        "Trading": [
            ("buy", "Place a buy order for a coin.", "`!buy <symbol> <price> <amount>`", "`!buy doge 3685 5`"),
            ("buy_list", "List all your active/pending buy orders.", "`!buy_list`", None),
            ("cancelbuy", "Cancel a specific buy order.", "`!cancelbuy <order_id>`", "`!cancelbuy DOGEIDR-123456`"),
            ("sell", "Place a sell order for a coin.", "`!sell <symbol> <price> <amount>`", "`!sell doge 4000 10`"),
            ("sell_list", "List all your active/pending sell orders.", "`!sell_list`", None),
            ("cancelsell", "Cancel a specific sell order.", "`!cancelsell <order_id>`", "`!cancelsell DOGEIDR-987654`"),
        ],
        "Admin": [
            ("maintenance", "Toggle maintenance mode (Owner only).", "`!maintenance on/off`", "`!maintenance on`"),
        ]
    }

    embed = Embed(
        title="🤖 Bot Command Reference",
        description="Here are the commands you can use, grouped by category:",
        color=0x00AAFF
    )

    for category, commands in command_categories.items():
        category_text = ""
        for name, desc, usage, example in commands:
            line = f"**{name}**: {desc}\nUsage: {usage}"
            if example:
                line += f"\nExample: {example}"
            category_text += line + "\n\n"
        embed.add_field(name=category, value=category_text, inline=False)

    embed.set_footer(text="Need more details? Contact the server admin.")
    await ctx.send(embed=embed)

@bot.command(name="ping")
async def ping(ctx):
    """Check both user ping (roundtrip) and bot latency in one embed."""
    before = time.monotonic()
    message = await ctx.send("🏓 Measuring ping...")
    user_ping = (time.monotonic() - before) * 1000
    bot_ping = bot.latency * 1000  # websocket latency in ms

    embed = discord.Embed(
        title="🏓 Pong!",
        color=discord.Color.green()
    )
    embed.add_field(name="👤 Your Ping", value=f"`{int(user_ping)} ms`", inline=True)
    embed.add_field(name="🤖 Bot Latency", value=f"`{int(bot_ping)} ms`", inline=True)
    embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)

    await message.edit(content=None, embed=embed)

@bot.command(name="crypto_news")
@maintenance_check()
@with_typing
async def crypto_news(ctx, limit: int = 100):
    articles = fetch_crypto_news(limit)
    if not articles:
        return await ctx.send("⚠️ No news found.")

    paginator = NewsPaginator(articles)
    embed     = paginator.get_embed()
    await ctx.send(embed=embed, view=paginator)

@bot.command(name="crypto_prices")
@maintenance_check()
@with_typing
async def crypto_prices(ctx):
    # Load all symbols from pairs.json
    try:
        with open("pairs.json", "r") as f:
            pairs = json.load(f)
        all_pairs = pairs["symbols"]
    except Exception as e:
        return await ctx.send(f"⚠️ Could not load pairs.json: {e}")

    paginator = PricesPaginator(all_pairs, fetch_price)
    embed = Embed(
        title="📊 Indodax Market",
        description="Latest market prices (from pairs.json)",
        color=0x2ECC71
    )

    # Limit to first 10 pairs (pagination comes next!)
    for pair in all_pairs[:10]:
        symbol, price = fetch_price(pair)
        if price:
            embed.add_field(
                name=symbol.upper(),
                value=f"Rp {price:,.0f}" if "idr" in pair else f"${price:,.2f}",
                inline=True
            )
        else:
            embed.add_field(
                name=symbol.upper(),
                value="❌ Error fetching price",
                inline=True
            )

    await ctx.send(embed=paginator.make_embed(), view=paginator)

# Analyze Command
# This command analyzes news sentiment and market activity to give buy/sell advice
@bot.command(
    name="analyze",
    help="!analyze <coin> <time> <unit> — analyze when to buy/sell based on news, trades, trend & prediction.\nExample: !analyze btc 2 hours"
)
@maintenance_check()
@with_typing
async def analyze(ctx, coin: str, *timeframe):
    import numpy as np
    import time as _time

    def fmt_pct(x, decimals=2):
        try:
            return f"{x:.{decimals}f}%"
        except Exception:
            return "—"

    def pct_change(a, b):
        try:
            return (a - b) / b * 100.0 if b else 0.0
        except Exception:
            return 0.0

    def safe_ratio(a, b):
        b = b if b else 1e-12
        return a / b

    # ---- Parse inputs ----
    coin = coin.lower().strip()
    pair = f"{coin}_idr"
    client = IndodaxClient()

    if not timeframe:  
        # Default to 1 hour
        time_value = 1
        seconds_ahead = 3600
        horizon_label = "1 hour"
    else:
        tf_str = " ".join(timeframe).lower().strip()  # handles "30m", "30 m", "30 minutes", "2h", "2 hours"

        # Match flexible formats
        match = re.match(r"^(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)$", tf_str)
        if not match:
            embed = discord.Embed(
                title="⚠️ Invalid Timeframe Format",
                description=(
                    "You entered an invalid timeframe.\n\n"
                    "**Valid formats:**\n"
                    "`30m`, `30 minutes`, `2h`, `2 hours`\n\n"
                    "**Examples:**\n"
                    "`!analyze btc 30m`\n"
                    "`!analyze btc 2 hours`\n"
                ),
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        time_value = int(match.group(1))
        time_unit = match.group(2)

        # Normalize unit map
        unit_map = {
            "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
            "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
        }
        seconds_ahead = time_value * unit_map[time_unit]

        # Normalize display (always use plural if > 1)
        if "m" in time_unit:
            horizon_label = f"{time_value} minute{'s' if time_value > 1 else ''}"
        else:
            horizon_label = f"{time_value} hour{'s' if time_value > 1 else ''}"

    # ---- News sentiment ----
    articles = fetch_crypto_news(10)
    if not articles:
        return await ctx.send(f"⚠️ Couldn’t fetch news for `{coin}` analysis.")

    pos_words = ["surge", "gain", "rally", "bull", "record", "up", "boost", "optimistic", "breakout", "institutional", "ETF", "upgrade", "partnership"]
    neg_words = ["drop", "dip", "slump", "bear", "decline", "down", "crash", "pessimistic", "hack", "ban", "probe", "lawsuit", "de-list"]
    pos_count = neg_count = 0
    for art in articles:
        title = (art.get("title") or "").lower()
        pos_count += sum(1 for w in pos_words if w in title)
        neg_count += sum(1 for w in neg_words if w in title)
    news_strength = "Bullish" if pos_count > neg_count else "Bearish" if neg_count > pos_count else "Neutral"

    # ---- Market trades ----
    try:
        trades = client.get_trades(pair, 500)  # [{date, type, price, amount, total}]
    except Exception as e:
        return await ctx.send(f"⚠️ Failed to fetch market data for `{coin}`: {e}")

    if not trades:
        return await ctx.send(f"No trades returned for `{pair}`.")

    # Ensure chronological order
    trades = sorted(trades, key=lambda t: float(t["date"]))

    # Extract arrays
    times = np.array([float(t["date"]) for t in trades], dtype=float)
    prices = np.array([float(t["price"]) for t in trades], dtype=float)
    amounts = np.array([float(t["amount"]) for t in trades], dtype=float)
    types = [t["type"] for t in trades]

    # Side splits
    buy_mask = np.array([tp == "buy" for tp in types])
    sell_mask = ~buy_mask

    buy_count = int(buy_mask.sum())
    sell_count = int(sell_mask.sum())
    buy_vol = float(amounts[buy_mask].sum()) if buy_count else 0.0
    sell_vol = float(amounts[sell_mask].sum()) if sell_count else 0.0
    avg_buy_size = buy_vol / buy_count if buy_count else 0.0
    avg_sell_size = sell_vol / sell_count if sell_count else 0.0
    flow_ratio = safe_ratio(buy_count, sell_count)  # >1 favors buyers

    # ---- Current ticker ----
    try:
        ticker = client.get_ticker(pair)
        current_price = float(ticker["ticker"]["last"])
    except Exception:
        current_price = prices[-1] if len(prices) else None

    # ---- Momentum (slope) & prediction ----
    predicted_price = None
    price_trend = None
    try:
        t0 = times.min()
        t_rel = times - t0  # seconds from first trade
        slope, intercept = np.polyfit(t_rel, prices, 1)  # price per second
        future_t = (t_rel.max() + seconds_ahead)
        predicted_price = float(slope * future_t + intercept)

        if current_price:
            # Convert slope to % per hour
            pct_per_hour = (slope * 3600.0) / current_price * 100.0
            if predicted_price > current_price * 1.02:
                price_trend = "Expected Rise"
            elif predicted_price < current_price * 0.98:
                price_trend = "Expected Drop"
            else:
                price_trend = "Flat"
        else:
            pct_per_hour = 0.0
    except Exception:
        predicted_price = None
        pct_per_hour = 0.0
        price_trend = None

    # ---- Trend (SMA crossover) ----
    sma_short = np.mean(prices[-50:]) if len(prices) >= 50 else np.mean(prices)
    sma_long = np.mean(prices[-200:]) if len(prices) >= 200 else np.mean(prices)
    trend_state = "Uptrend" if sma_short >= sma_long else "Downtrend" if sma_short < sma_long else "Sideways"

    # ---- Volatility & range posture ----
    lookback = prices[-200:] if len(prices) >= 200 else prices
    volatility_pct = np.std(lookback) / np.mean(lookback) * 100.0 if len(lookback) > 1 else 0.0
    recent = prices[-100:] if len(prices) >= 100 else prices
    rng_low, rng_high = float(np.min(recent)), float(np.max(recent))
    if current_price:
        range_pos_pct = (current_price - rng_low) / (rng_high - rng_low) * 100.0 if rng_high > rng_low else 50.0
    else:
        range_pos_pct = 50.0

    # ---- Recent window comparison (acceleration) ----
    now_ts = times.max()
    recent_window = 30 * 60  # 30 minutes
    prev_cut = now_ts - 2 * recent_window
    mid_cut = now_ts - recent_window

    prev_mask = (times >= prev_cut) & (times < mid_cut)
    recent_mask = (times >= mid_cut)

    def side_stats(mask):
        if not mask.any():
            return 0, 0.0
        sub_types = np.array(types)[mask]
        sub_amounts = amounts[mask]
        b = float(sub_amounts[sub_types == "buy"].sum())
        s = float(sub_amounts[sub_types == "sell"].sum())
        return b, s

    recent_b_vol, recent_s_vol = side_stats(recent_mask)
    prev_b_vol, prev_s_vol = side_stats(prev_mask)
    buy_accel = pct_change(recent_b_vol, prev_b_vol) if prev_b_vol else (100.0 if recent_b_vol > 0 else 0.0)
    sell_accel = pct_change(recent_s_vol, prev_s_vol) if prev_s_vol else (100.0 if recent_s_vol > 0 else 0.0)

    # ---- Scoring & confidence ----
    score = 0
    score += 1 if news_strength == "Bullish" else -1 if news_strength == "Bearish" else 0
    score += 1 if flow_ratio > 1.1 else -1 if flow_ratio < 0.9 else 0
    score += 1 if avg_buy_size > avg_sell_size * 1.1 else -1 if avg_buy_size * 1.1 < avg_sell_size else 0
    score += 1 if pct_per_hour > 1.0 else -1 if pct_per_hour < -1.0 else 0
    score += 1 if trend_state == "Uptrend" else -1 if trend_state == "Downtrend" else 0
    # Range posture: buying low in range or selling high in range is favorable
    if range_pos_pct <= 30.0:
        score += 1  # near support
    elif range_pos_pct >= 70.0:
        score -= 1  # near resistance

    # Confidence weighting
    confidence = 50
    confidence += 10 if news_strength != "Neutral" else 0
    confidence += 10 if abs(pct_per_hour) >= 1.0 else 0
    confidence += 10 if abs(flow_ratio - 1.0) >= 0.2 else 0
    confidence -= 10 if volatility_pct >= 5.0 else 0
    confidence -= 10 if len(trades) < 150 else 0
    confidence = max(5, min(95, confidence))

    # Final advice
    if score >= 3:
        advice, color = "📈 Strong Buy", 0x2ECC71
    elif score == 2:
        advice, color = "✅ Buy", 0x2ECC71
    elif score == 1 or score == 0:
        advice, color = "🤔 Hold", 0xF1C40F
    elif score == -1:
        advice, color = "⚠️ Sell", 0xE74C3C
    else:
        advice, color = "🚨 Strong Sell", 0xE74C3C

        # ---- Entry/Exit/Stoploss ----
    entry_price = rng_low  # Support
    exit_price = rng_high  # Resistance
    if predicted_price and predicted_price > current_price:
        exit_price = predicted_price  # use prediction if higher
    stoploss_price = rng_low * 0.97 if rng_low else None  # 3% below support

    # ---- Reasoning text ----
    bullets = []
    bullets.append(f"📰 **News:** `{news_strength}` (🟢 {pos_count} positive / 🔴 {neg_count} negative).")
    bullets.append(f"📊 **Order Flow:** {buy_count} buys vs {sell_count} sells "
                   f"(ratio {safe_ratio(buy_count, sell_count):.2f}; "
                   f"avg size {avg_buy_size:.6f} vs {avg_sell_size:.6f}).")
    if prev_b_vol or prev_s_vol:
        bullets.append(f"⚡ **Recent Shift (30m):** Buy vol {fmt_pct(buy_accel)} vs Sell vol {fmt_pct(sell_accel)} vs previous 30m.")
    bullets.append(f"📈 **Momentum:** ~{fmt_pct(pct_per_hour)} per hour "
                   f"({('rise' if pct_per_hour>0 else 'drop') if abs(pct_per_hour)>=0.1 else 'flat'}).")
    bullets.append(f"🧭 **Trend (SMA 50/200):** {trend_state} (SMA50={sma_short:,.0f}, SMA200={sma_long:,.0f}).")
    bullets.append(f"📐 **Volatility:** {fmt_pct(volatility_pct)} (higher reduces confidence).")
    if current_price is not None:
        bullets.append(f"📦 **Range Position (last 100 trades):** {fmt_pct(range_pos_pct)} of range "
                       f"[{rng_low:,.0f}–{rng_high:,.0f}] (lower=near support).")
    if predicted_price and current_price:
        ppct = pct_change(predicted_price, current_price)
        bullets.append(f"🔮 **Prediction ({horizon_label}):** {predicted_price:,.2f} IDR "
                       f"({fmt_pct(ppct)}) based on linear trend.")
    else:
        bullets.append("🔮 **Prediction:** Not available due to insufficient data.")

    bullets.append(f"🧠 **Confidence:** {confidence}/100 (data quality, trend strength, and volatility adjusted).")

    reasoning_text = "\n".join(bullets)

    # ---- Build embed ----
    embed = discord.Embed(
        title=f"🔍 Analysis for {coin.upper()}",
        description=f"**Advice:** {advice}\n\n**Why this advice:**\n{reasoning_text}",
        color=color,
        timestamp=ctx.message.created_at
    )

    # Add prices
    if current_price is not None:
        embed.add_field(name="💵 Current Price", value=f"{current_price:,.2f} IDR", inline=True)
    if entry_price is not None:
        embed.add_field(name="🎯 Entry Price", value=f"{entry_price:,.2f} IDR", inline=True)
    if exit_price is not None:
        embed.add_field(name="💰 Exit Price", value=f"{exit_price:,.2f} IDR", inline=True)
    if stoploss_price is not None:
        embed.add_field(name="🛑 Stoploss", value=f"{stoploss_price:,.2f} IDR", inline=True)

    # Top headlines
    top_titles = "\n".join(f"• {(a.get('title') or '')[:120]}" for a in articles[:3])
    if top_titles.strip():
        embed.add_field(name="📰 Top News Headlines", value=top_titles, inline=False)

    embed.set_footer(text=f"Data: 10 news items & {len(trades)} trades | Horizon: {horizon_label}")

    await ctx.send(embed=embed)

# Market Command
@bot.command(
    name="market",
    help="!market <coin> [limit] — fetch up to 500 trades, display top 10 in an Embed"
)
@maintenance_check()
@with_typing
async def market(ctx, coin: str, limit: int = 500):
    coin     = coin.lower()
    MAX_FETCH = 500
    SHOW_ROWS = 10

    # Validate limit
    if limit < 1 or limit > MAX_FETCH:
        return await ctx.send(f"⚠️ Limit must be between 1 and {MAX_FETCH}.")

    pair   = f"{coin}_idr"
    client = IndodaxClient()

    # 1) Fetch trades
    try:
        trades = client.get_trades(pair, limit)
    except Exception as e:
        return await ctx.send(f"⚠️ Failed to fetch trades for `{pair}`: {e}")

    if not trades:
        return await ctx.send(f"No trades returned for `{pair}`.")

    # 2) Tally Buys vs Sells
    buy_count  = sum(1 for t in trades if t["type"] == "buy")
    sell_count = sum(1 for t in trades if t["type"] == "sell")

    # 3) Build table of only the first SHOW_ROWS trades
    visible = trades[:SHOW_ROWS]
    table   = PrettyTable(["Time", "Type", "Price (IDR)", coin.upper()])
    for t in visible:
        ts      = int(float(t["date"]))
        timestr = time.strftime("%H:%M:%S", time.localtime(ts))
        table.add_row([
            timestr,
            t["type"].capitalize(),
            f"{float(t['price']):,.0f}",
            f"{float(t['amount']):.6f}"
        ])

    # 4) Build a Discord Embed
    color = 0x2ECC71 if buy_count > sell_count else 0xE73C4C
    embed = discord.Embed(
        title=f"{coin.upper()} Market Trades",
        description=f"```{table.get_string()}```",
        color=color,
        timestamp=ctx.message.created_at
    )
    embed.add_field(
        name="Summary",
        value=(
            f"📈 Buys: **{buy_count}**\n"
            f"📉 Sells: **{sell_count}**\n"
            f"⚡ Fetched: {len(trades)} trades"
        ),
        inline=False
    )
    embed.set_footer(
        text=f"Displayed: {SHOW_ROWS} trades • Requested by {ctx.author.display_name}",
        icon_url=ctx.author.avatar.url if ctx.author.avatar else None
    )

    await ctx.send(embed=embed)

@bot.command(name="trending")
@maintenance_check()
@with_typing
async def trending(ctx):
    coins = fetch_trending_coins()
    if not coins:
        return await ctx.send("⚠️ Could not fetch trending coins right now.")

    embed = Embed(
        title="🔥 Trending Cryptocurrencies",
        description="Powered by CoinGecko",
        color=0xF7931A
    )

    for coin in coins:
        url = f"https://www.coingecko.com/en/coins/{coin['id']}"
        embed.add_field(
            name=f"{coin['name']} ({coin['symbol'].upper()})",
            value=(
                f"Rank: #{coin['market_cap_rank']}\n"
                f"Price (BTC): {coin['price_idr']:.8f}\n"
                f"[View on CoinGecko]({url})"
            ),
            inline=False
        )

    embed.set_thumbnail(url=coins[0]["thumb"])
    await ctx.send(embed=embed)

@bot.command(name="setkeys")
@maintenance_check()
@with_typing
async def setkeys(ctx, api_key: str = None, api_secret: str = None):
    # If command is in a public channel, delete immediately
    if ctx.guild is not None:
        try:
            await ctx.message.delete()
        except:
            pass

    # If no keys provided, send safe embed instructions in DM
    if not api_key or not api_secret:
        embed = discord.Embed(
            title="❌ Command Usage Error",
            description=(
                "Usage: `!setkeys YOUR_API_KEY YOUR_API_SECRET` in a **DM** to the bot.\n\n"
                "How to get API Key? [Click Here](https://indodax.com/trade_api)"
            ),
            color=discord.Color.red()
        )
        embed.set_footer(text="Your keys are private — never share them in public channels.")

        try:
            await ctx.author.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(f"{ctx.author.mention} I couldn't DM you. Please enable Direct Messages.")
        return

    # Save credentials securely
    creds = load_credentials()
    creds[str(ctx.author.id)] = {
        "api_key": api_key,
        "api_secret": api_secret
    }
    save_credentials(creds)

    # Confirmation embed
    embed = discord.Embed(
        title="✅ API Keys Saved",
        description="Your Indodax API credentials have been saved securely!",
        color=discord.Color.green()
    )
    await ctx.author.send(embed=embed)

@bot.command(name="balance")
@maintenance_check()
@with_typing
async def balance(ctx):
    # 1) Load user creds
    creds   = load_credentials()
    user_id = str(ctx.author.id)
    if user_id not in creds:
        return await ctx.send(
            "❌ You haven’t set your Indodax keys yet.\n"
            "Please DM me: `!setkeys YOUR_API_KEY YOUR_API_SECRET`"
        )

    # 2) Instantiate client with user’s keys
    data   = creds[user_id]
    client = IndodaxClient(api_key=data["api_key"], api_secret=data["api_secret"])
    info         = client.get_account_info()["return"]
    free_bal     = info["balance"]
    hold_bal     = info["balance_hold"]


    # 3) Fetch account info & extract balances
    try:
        info     = client.get_account_info()
        balances = info["return"]["balance"]
    except Exception as e:
        return await ctx.send(f"⚠️ Failed to fetch balances: {e}")
    all_coins = {}
    for coin, amt in free_bal.items():
        all_coins[coin] = float(amt) + float(hold_bal.get(coin, 0))



    embed = discord.Embed(
    title=f"💰 {ctx.author.display_name}'s Indodax Balances",
    color=discord.Color.blue()
)

    for coin, total in all_coins.items():
        if total > 0:
            embed.add_field(
                name=coin.upper(),
                value=f"{total:.8f}",
                inline=True
            )

    # 5) Send as a DM (and optionally ack in channel)
    try:
        await ctx.author.send(embed=embed)
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("📬 I’ve sent you a DM with your balances!")
    except discord.Forbidden:
        await ctx.send("❌ I can’t DM you. Please enable DMs from this server.")

@bot.command(name="alert")
@maintenance_check()
@with_typing
async def set_alert(ctx, symbol: str, change: str):
    """
    Usage:
      !alert SYMBOL PRICE       → absolute price in IDR
      !alert SYMBOL +PERCENT%   → relative change from current price
    """
    user_id = str(ctx.author.id)
    sym_up = symbol.upper()
    pair_key = symbol.lower() if symbol.lower().endswith("_idr") else f"{symbol.lower()}_idr"

    # Clean input
    change_clean = change.strip()

    percent_val = None
    target_price = None

    try:
        if change_clean.endswith("%"):
            try:
                percent_val = float(change_clean.rstrip("%"))
            except ValueError:
                return await ctx.send(embed=discord.Embed(
                    title="❌ Invalid Format",
                    description="Percentage format should look like `+10%` or `-5%`.",
                    color=discord.Color.red()
                ))

            try:
                # Convert doge_idr → dogeidr for Indodax
                current_price = get_last_price(pair_key.replace("_", ""))
            except Exception as e:
                return await ctx.send(embed=discord.Embed(
                    title="❌ Price Fetch Error",
                    description=f"Couldn’t fetch current price for `{pair_key}`.\n{e}",
                    color=discord.Color.red()
                ))

            target_price = current_price * (1 + percent_val / 100)

        else:
            # Absolute price
            try:
                target_price = float(change_clean)
            except ValueError:
                return await ctx.send(embed=discord.Embed(
                    title="❌ Invalid Format",
                    description="Please use an absolute number like `900000` or a percentage like `+10%`.",
                    color=discord.Color.red()
                ))

    except Exception as e:
        return await ctx.send(embed=discord.Embed(
            title="❌ Unexpected Error",
            description=str(e),
            color=discord.Color.red()
        ))

    # Persist the alert
    alerts = load_alerts()
    user_alerts = alerts.setdefault(user_id, [])
    user_alerts.append({
        "pair":    pair_key,
        "target":  target_price,
        "percent": percent_val  # None if absolute
    })
    save_alerts(alerts)

    # Confirmation
    desc = f"**{pair_key}** at `{target_price:,.2f}` IDR"
    if percent_val is not None:
        desc += f"  ({percent_val:+.2f}%)"

    embed = discord.Embed(
        title="✅ Alert Set",
        description=desc,
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)



@bot.command(name="alert_list")
@maintenance_check()
@with_typing
async def alert_list(ctx):
    
    alerts = load_alerts()               # your JSON/YAML/DB loader
    user_id = str(ctx.author.id)
    
    if user_id not in alerts or not alerts[user_id]:
        return await ctx.send(embed=discord.Embed(
            title="🔔 You Have No Alerts",
            description="Use `!alert <symbol> +10%` or `!alert <symbol> PRICE` to add one.",
            color=discord.Color.blue()
        ))
    
    lines = []
    for idx, entry in enumerate(alerts[user_id], start=1):
        pair    = entry["pair"]
        target  = entry["target"]
        percent = entry.get("percent")
        
        # Format price and percent
        price_str   = f"{target:,.2f} IDR"
        percent_str = f" ({percent:+.2f}%)" if percent is not None else ""
        
        lines.append(f"{idx}. **{pair}** at \n`{price_str}{percent_str}`")
    
    embed = discord.Embed(
        title="🔔 Your Price Alerts",
        description="\n".join(lines),
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

@bot.command(name="remove_alert")
@maintenance_check()
@with_typing
async def remove_alert(ctx, index: int):
    user_id = str(ctx.author.id)
    alerts = load_alerts()

    if user_id not in alerts or index < 1 or index > len(alerts[user_id]):
        return await ctx.send("❌ Invalid alert index.")

    removed = alerts[user_id].pop(index - 1)
    save_alerts(alerts)

    await ctx.send(f"🗑️ Removed alert for `{removed['pair']}` target at `{removed['target']}` IDR")

    
@bot.command(name="pairs")
@maintenance_check()
@with_typing
async def list_pairs(ctx):
    try:
        from alert_storage import get_pairs  # or wherever you defined get_pairs()
        all_pairs = get_pairs()

        paginator = PairsPaginator(all_pairs, per_page=25)
        embed = paginator.make_embed()

        await ctx.send(embed=embed, view=paginator)

    except FileNotFoundError:
        await ctx.send("❌ `pairs.json` not found. Run `update_pairs.py` first.")
    except Exception as e:
        await ctx.send(f"❌ Error loading pairs: {e}")

@bot.command(name="buy")
@maintenance_check()
@with_typing
async def buy_command(ctx, coin: str, price: float, amount: float):
    client = IndodaxClient()
    pair = f"{coin.lower()}_idr"
    total_idr = price * amount

    try:
        order = client.create_buy_order(pair, price, amount)
        order_id = order['return']['order_id']

        # Store order locally
        data = load_pending_orders()
        user_orders = data.get(str(ctx.author.id), [])
        user_orders.append({
            "order_id": order_id,
            "pair": pair,
            "price": price,
            "amount": amount,
            "total": total_idr,
            "status": "pending"
        })
        data[str(ctx.author.id)] = user_orders
        save_pending_orders(data)

        # Send confirmation embed
        embed = discord.Embed(
            title="✅ Buy Order Placed",
            description=f"You have placed a buy order for **{amount} {coin.upper()}** at **Rp {price:,.0f}** each.",
            color=0x2ECC71
        )
        embed.add_field(name="Pair", value=f"{coin.upper()}/IDR", inline=True)
        embed.add_field(name="Total", value=f"Rp {total_idr:,.0f}", inline=True)
        embed.add_field(name="Order ID", value=str(order_id), inline=False)
        embed.set_footer(text="Order may still be pending depending on market conditions.")
        await ctx.send(embed=embed)

    except ValueError as e:
        embed = discord.Embed(
            title="❌ Error Placing Buy Order",
            description=str(e),
            color=0xE74C3C
        )
        await ctx.send(embed=embed)
    except RuntimeError as e:
        embed = discord.Embed(
            title="❌ Error Placing Buy Order",
            description=str(e),
            color=0xE74C3C
        )
        await ctx.send(embed=embed)


@bot.command(name="buy_list")
@maintenance_check()
@with_typing
async def buy_list_command(ctx):
    data = load_pending_orders()

    # If file is empty or user has no orders
    orders = data.get(str(ctx.author.id), [])
    if not orders:
        embed = discord.Embed(
            title="📋 Pending Buy Orders",
            description="You have no pending buy orders.",
            color=0x3498DB
        )
        await ctx.send(embed=embed)
        return

    # If there are orders, display them
    embed = discord.Embed(
        title="📋 Pending Buy Orders",
        color=0x3498DB
    )
    for o in orders:
        embed.add_field(
            name=f"Order #{o['order_id']}",
            value=f"Pair: **{o['pair']}**\nPrice: **{o['price']}**\nAmount: **{o['amount']}**\nTotal: **{o['total']}**\nStatus: **{o['status']}**",
            inline=False
        )
    await ctx.send(embed=embed)


@bot.command(name="cancelbuy")
@maintenance_check()
@with_typing
async def cancel_buy_command(ctx, order_id: str):
    data = load_pending_orders()
    user_orders = data.get(str(ctx.author.id), [])
    
    # Find the order to cancel
    order_to_cancel = None
    for o in user_orders:
        if str(o["order_id"]) == str(order_id):
            order_to_cancel = o
            break

    if not order_to_cancel:
        embed = discord.Embed(
            title="❌ Order Not Found",
            description=f"No pending order with ID {order_id} found.",
            color=0xE74C3C
        )
        await ctx.send(embed=embed)
        return

    client = IndodaxClient()
    try:
        # Cancel on Indodax using the pair from the order
        client.cancel_order(order_to_cancel["pair"], order_id, "buy")

        # Remove from local pending_orders.json
        data[str(ctx.author.id)] = [o for o in user_orders if str(o["order_id"]) != str(order_id)]
        save_pending_orders(data)

        embed = discord.Embed(
            title="✅ Buy Order Cancelled",
            description=f"Order #{order_id} has been successfully cancelled.",
            color=0x2ECC71
        )
        await ctx.send(embed=embed)

    except RuntimeError as e:
        embed = discord.Embed(
            title="❌ Error Cancelling Buy Order",
            description=str(e),
            color=0xE74C3C
        )
        await ctx.send(embed=embed)

@bot.command(name="sell")
@maintenance_check()
@with_typing
async def sell_command(ctx, coin: str, price: float, amount: float):
    client = IndodaxClient()
    pair = f"{coin.lower()}_idr"
    total_idr = price * amount

    try:
        order = client.create_sell_order(pair, price, amount)
        order_id = order['return']['order_id']

        # Store order locally
        data = load_pending_orders()
        user_orders = data.get(str(ctx.author.id), [])
        user_orders.append({
            "order_id": order_id,
            "pair": pair,
            "price": price,
            "amount": amount,
            "total": total_idr,
            "status": "pending",
            "type": "sell"  # track type
        })
        data[str(ctx.author.id)] = user_orders
        save_pending_orders(data)

        # Confirmation embed
        embed = discord.Embed(
            title="✅ Sell Order Placed",
            description=f"You have placed a sell order for **{amount} {coin.upper()}** at **Rp {price:,.0f}** each.",
            color=0xF1C40F
        )
        embed.add_field(name="Pair", value=f"{coin.upper()}/IDR", inline=True)
        embed.add_field(name="Total", value=f"Rp {total_idr:,.0f}", inline=True)
        embed.add_field(name="Order ID", value=str(order_id), inline=False)
        embed.set_footer(text="Order may still be pending depending on market conditions.")
        await ctx.send(embed=embed)

    except ValueError as e:
        embed = discord.Embed(
            title="❌ Error Placing Sell Order",
            description=str(e),
            color=0xE74C3C
        )
        await ctx.send(embed=embed)
    except RuntimeError as e:
        embed = discord.Embed(
            title="❌ Error Placing Sell Order",
            description=str(e),
            color=0xE74C3C
        )
        await ctx.send(embed=embed)

@bot.command(name="sell_list")
@maintenance_check()
@with_typing
async def sell_list_command(ctx):
    data = load_pending_orders()
    orders = [o for o in data.get(str(ctx.author.id), []) if o.get("type") == "sell"]

    if not orders:
        embed = discord.Embed(
            title="📋 Pending Sell Orders",
            description="You have no pending sell orders.",
            color=0xF1C40F
        )
        await ctx.send(embed=embed)
        return

    embed = discord.Embed(
        title="📋 Pending Sell Orders",
        color=0xF1C40F
    )
    for o in orders:
        embed.add_field(
            name=f"Order #{o['order_id']}",
            value=f"Pair: **{o['pair']}**\nPrice: **{o['price']}**\nAmount: **{o['amount']}**\nTotal: **{o['total']}**\nStatus: **{o['status']}**",
            inline=False
        )
    await ctx.send(embed=embed)

@bot.command(name="cancelsell")
@maintenance_check()
@with_typing
async def cancel_sell_command(ctx, order_id: str):
    data = load_pending_orders()
    user_orders = data.get(str(ctx.author.id), [])
    
    # Find sell order
    order_to_cancel = None
    for o in user_orders:
        if str(o["order_id"]) == str(order_id) and o.get("type") == "sell":
            order_to_cancel = o
            break

    if not order_to_cancel:
        embed = discord.Embed(
            title="❌ Order Not Found",
            description=f"No pending sell order with ID {order_id} found.",
            color=0xE74C3C
        )
        await ctx.send(embed=embed)
        return

    client = IndodaxClient()
    try:
        client.cancel_order(order_to_cancel["pair"], order_id, "sell")

        # Remove from local pending orders
        data[str(ctx.author.id)] = [o for o in user_orders if str(o["order_id"]) != str(order_id)]
        save_pending_orders(data)

        embed = discord.Embed(
            title="✅ Sell Order Cancelled",
            description=f"Order #{order_id} has been successfully cancelled.",
            color=0x2ECC71
        )
        await ctx.send(embed=embed)

    except RuntimeError as e:
        embed = discord.Embed(
            title="❌ Error Cancelling Sell Order",
            description=str(e),
            color=0xE74C3C
        )
        await ctx.send(embed=embed)

@bot.command(name="auto_stoploss", help="!auto_stoploss <coin> <percent>")
@maintenance_check()
@with_typing
async def auto_stoploss(ctx, coin: str, percent: float):
    pair = f"{coin.lower()}_idr"
    try:
        current_price = get_last_price(pair)
        stop_price = current_price * (1 - percent / 100.0)

        stoploss_entry = {
            "coin": coin.upper(),
            "pair": pair,
            "stop_price": stop_price,
            "percent": percent,
            "user": ctx.author.id,
            "active": True
        }

        # Save to pending_orders.json
        try:
            with open("pending_orders.json", "r+") as f:
                data = json.load(f)
                data.setdefault("stoploss", []).append(stoploss_entry)
                f.seek(0)
                json.dump(data, f, indent=2)
        except FileNotFoundError:
            with open("pending_orders.json", "w") as f:
                json.dump({"stoploss": [stoploss_entry]}, f, indent=2)

        await ctx.send(
            f"🛑 Stoploss set for {coin.upper()} at {stop_price:,.0f} IDR "
            f"({percent:.1f}% below current {current_price:,.0f})"
        )
    except Exception as e:
        await ctx.send(f"⚠️ Failed to set stoploss: {e}")

@bot.command(
    name="trade_history",
    help="!trade_history <coin> [count] — Show your recent trades for a coin."
)
@with_typing
async def trade_history(ctx, coin: str, count: int = 10):
    client = IndodaxClient()
    try:
        pair = f"{coin.lower()}_idr"
        trades = client.get_trade_history(pair, count)

        if "return" not in trades or "trades" not in trades["return"]:
            await ctx.send(f"⚠️ No trade history found for {coin.upper()}.")
            return

        trade_list = trades["return"]["trades"]
        if not trade_list:
            await ctx.send(f"⚠️ No trades found for {coin.upper()}.")
            return

        # Split trades into pages (max 5 trades per page to keep messages short)
        trades_per_page = 5
        pages = math.ceil(len(trade_list) / trades_per_page)

        async def send_page(page: int):
            start = page * trades_per_page
            end = start + trades_per_page
            subset = trade_list[start:end]

            msg = f"📊 Trade History for **{coin.upper()}** (Page {page+1}/{pages}):\n"
            for t in subset:
                side = "🟢 BUY" if t["type"] == "buy" else "🔴 SELL"
                msg += (
                    f"- {side} {t['amount']} @ {t['rate']} "
                    f"(Fee: {t.get('fee', 'N/A')})\n"
                )
            return msg

        # Send first page
        current_page = 0
        message = await ctx.send(await send_page(current_page))

        # Add navigation reactions
        if pages > 1:
            await message.add_reaction("⬅️")
            await message.add_reaction("➡️")

            def check(reaction, user):
                return (
                    user == ctx.author
                    and reaction.message.id == message.id
                    and str(reaction.emoji) in ["⬅️", "➡️"]
                )

            while True:
                try:
                    reaction, user = await bot.wait_for(
                        "reaction_add", timeout=60.0, check=check
                    )

                    if str(reaction.emoji) == "➡️" and current_page < pages - 1:
                        current_page += 1
                        await message.edit(content=await send_page(current_page))

                    elif str(reaction.emoji) == "⬅️" and current_page > 0:
                        current_page -= 1
                        await message.edit(content=await send_page(current_page))

                    await message.remove_reaction(reaction, user)

                except asyncio.TimeoutError:
                    break  # stop listening after 60s of inactivity

    except Exception as e:
        await ctx.send(f"❌ Error fetching trade history: {e}")

if __name__ == "__main__":
    bot.run(TOKEN)
