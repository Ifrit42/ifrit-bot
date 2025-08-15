import os
import json
import re
import time
import discord
from discord.ext import commands
from discord import Embed
from prettytable import PrettyTable
from dotenv import load_dotenv
import numpy as np
from functools import wraps
import asyncio
import requests


from alert_storage import load_alerts, save_alerts, get_pairs
from pending_storage import load_pending_orders, save_pending_orders, add_pending_order, remove_pending_order_by_user
from price_fetcher import get_last_price
from indodax_api      import IndodaxClient
from news_fetcher   import fetch_crypto_news
from paginator      import NewsPaginator, PairsPaginator
from coingecko import fetch_trending_coins
from price_analysis   import fetch_crypto_prices
from quota_calculator import (
    calculate_buy_quota,
    calculate_sell_quota,
    count_market_activity,
    get_coin_balance
)

CRED_FILE = "user_credentials.json"

MAINTENANCE_MODE = False  # Change to False to disable
BOT_OWNERS = [527832667845033994, 1402691770545995796]

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
    async def wrapper(ctx, *args, **kwargs):
        async def keep_typing():
            try:
                while True:
                    await ctx.trigger_typing()
                    await asyncio.sleep(5)  # send typing signal every 5 sec
            except asyncio.CancelledError:
                pass

        typing_loop = asyncio.create_task(keep_typing())
        try:
            return await func(ctx, *args, **kwargs)
        finally:
            typing_loop.cancel()
    return wrapper

def get_user_alerts(user_id: str):
    alerts = load_alerts()
    return alerts.get(user_id, [])

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
            usage_msg = "❌ Usage: `!analyze <coin>` to analyze when to buy/sell based on news & market stats."
        elif ctx.command.name == "trending":
            usage_msg = "❌ Usage: `!trending` to show the current top trending cryptocurrencies."
        elif ctx.command.name == "balance":
            usage_msg = "❌ Usage: `!balance` to check your Indodax account balance."
        elif ctx.command.name == "pairs":
            usage_msg = "❌ Usage: `!pairs` to list all trading pairs."
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
            ("analyze", "Analyze when to buy/sell based on news & market stats.", "`!analyze <coin>`", "`!analyze floki`"),
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
    """!crypto_prices — fetch current top coin prices."""
    prices = fetch_crypto_prices()
    for symbol, price in prices.items():
        await ctx.send(f"{symbol.upper()}: Rp {price}")

# Analyze Command
# This command analyzes news sentiment and market activity to give buy/sell advice
@bot.command(
    name="analyze",
    help="!analyze <coin> — analyze when to buy/sell based on news sentiment, market activity, current & predicted price"
)
@maintenance_check()
@with_typing
async def analyze(ctx, coin: str):
    coin = coin.lower()
    pair = f"{coin}_idr"
    client = IndodaxClient()

    # 1) Fetch top 10 news articles
    articles = fetch_crypto_news(10)
    if not articles:
        return await ctx.send(f"⚠️ Couldn’t fetch news for `{coin}` analysis.")

    # 2) Naive sentiment analysis on headlines
    pos_words = ["surge", "gain", "rally", "bull", "record", "up", "boost", "optimistic"]
    neg_words = ["drop", "dip", "slump", "bear", "decline", "down", "crash", "pessimistic"]
    pos_count = neg_count = 0
    for art in articles:
        title = art["title"].lower()
        for w in pos_words:
            if w in title:
                pos_count += 1
        for w in neg_words:
            if w in title:
                neg_count += 1

    # 3) Fetch the last 500 trades and tally buys vs sells
    try:
        trades = client.get_trades(pair, 500)
    except Exception as e:
        return await ctx.send(f"⚠️ Failed to fetch market data for `{coin}`: {e}")

    buy_count  = sum(1 for t in trades if t["type"] == "buy")
    sell_count = sum(1 for t in trades if t["type"] == "sell")

    # 4) Get current price
    try:
        ticker = client.get_ticker(pair)
        current_price = float(ticker["ticker"]["last"])
    except Exception as e:
        current_price = None

    # 5) Simple 1‐hour‐ahead linear regression on trade history
    predicted_price = None
    try:
        times = np.array([float(t["date"]) for t in trades])
        prices = np.array([float(t["price"]) for t in trades])
        t0 = times.min()
        times -= t0

        slope, intercept = np.polyfit(times, prices, 1)

        hours_ahead = 1200 # 3600 = 1 hour in seconds
        future_t = (times.max() + hours_ahead)
        predicted_price = slope * future_t + intercept
    except Exception:
        predicted_price = None

    if pos_count > neg_count and buy_count > sell_count:
        advice, color = "Strong Buy", 0x2ECC71
    elif pos_count > neg_count:
        advice, color = "Buy", 0x2ECC71
    elif neg_count > pos_count and sell_count > buy_count:
        advice, color = "Strong Sell", 0xE74C3C
    elif neg_count > pos_count:
        advice, color = "Sell", 0xE74C3C
    else:
        advice, color = "Hold", 0xF1C40F

    embed = discord.Embed(
        title=f"🔍 Analysis for {coin.upper()}",
        color=color,
        timestamp=ctx.message.created_at
    )

    embed.add_field(name="Advice", value=f"**{advice}**", inline=False)
    embed.add_field(
        name="News Sentiment",
        value=f"🟢 Positive: {pos_count}\n🔴 Negative: {neg_count}",
        inline=False
    )
    embed.add_field(
        name="Market Activity (last 500 trades)",
        value=f"🟢 Buys: {buy_count}\n🔴 Sells: {sell_count}",
        inline=False
    )
    news_score   = pos_count - neg_count  
    market_score = (buy_count > sell_count) - (sell_count > buy_count)  
    pred_signal  = 0
    if predicted_price and current_price:
        pred_signal = 1 if predicted_price > current_price else -1

    total_score = news_score + market_score + pred_signal

    if predicted_price and current_price:
        pct = (predicted_price - current_price) / current_price
        if pct < -0.02:
            advice, color = "Sell", 0xE74C3C
        else:
            if total_score >= 2:
                advice, color = "Strong Buy", 0x2ECC71
            elif total_score == 1:
                advice, color = "Buy",        0x2ECC71
            elif total_score == 0:
                advice, color = "Hold",       0xF1C40F
            elif total_score == -1:
                advice, color = "Sell",       0xE74C3C
            else:
                advice, color = "Strong Sell",0xE74C3C
    else:
        if pos_count > neg_count and buy_count > sell_count:
            advice, color = "Strong Buy", 0x2ECC71


    if current_price is not None:
        embed.add_field(
            name="Current Price",
            value=f"₿ {current_price:,.2f} IDR",
            inline=True
        )
    if predicted_price is not None:
        embed.add_field(
            name="Predicted Price (20 mins)",
            value=f"₿ {predicted_price:,.2f} IDR",
            inline=True
        )

    top_titles = "\n".join(f"• {a['title']}" for a in articles[:3])
    embed.add_field(name="Top News Headlines", value=top_titles, inline=False)
    embed.set_footer(text="Analyzed from 10 articles & 500 trades")

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
                f"Price (BTC): {coin['price_btc']:.8f}\n"
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

if __name__ == "__main__":
    bot.run(TOKEN)
