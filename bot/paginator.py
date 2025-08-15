import discord
from discord import ButtonStyle
from discord.ui import View, button
from datetime import datetime

class NewsPaginator(View):
    def __init__(self, articles: list[dict]):
        super().__init__(timeout=120)
        self.articles = articles
        self.index = 0

        # disable “Previous” on first page
        self.prev_button.disabled = True
        # disable “Next” if there’s only one article
        if len(self.articles) <= 1:
            self.next_button.disabled = True

    def get_embed(self) -> discord.Embed:
        art = self.articles[self.index]
        embed = discord.Embed(
            title=f"📰 {art['title']}",
            url=art['url'],
            description=(art['description'][:200] + "...") if art['description'] else "",
            color=0x00FF99,
            timestamp=datetime.fromisoformat(art['published'])
        )
        embed.set_thumbnail(url=art.get('thumbnail_url'))
        # if the key is named 'publisher'
        embed.add_field(name="Source", value=art.get('publisher', 'Coindesk - Unknown Publisher'), inline=True)
        embed.add_field(
            name="Published",
            value=f"<t:{int(embed.timestamp.timestamp())}:R>",
            inline=True
        )
        embed.set_footer(text=f"Article {self.index+1}/{len(self.articles)}")
        return embed

    @button(label="Previous", style=ButtonStyle.gray, custom_id="prev_button")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # move back one
        self.index -= 1
        # enable Next, disable Previous if at start
        self.next_button.disabled = False
        button.disabled = (self.index == 0)
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @button(label="Next", style=ButtonStyle.blurple, custom_id="next_button")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # move forward one
        self.index += 1
        # enable Previous, disable Next if at end
        self.prev_button.disabled = False
        button.disabled = (self.index == len(self.articles) - 1)
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

class PairsPaginator(discord.ui.View):
    def __init__(self, pairs: list[str], per_page: int = 50):
        super().__init__(timeout=120)
        self.pairs = pairs
        self.per_page = per_page
        self.page = 0
        self.total_pages = (len(pairs) - 1) // per_page + 1

    def make_embed(self) -> discord.Embed:
        # Determine slice for this page
        start = self.page * self.per_page
        end = start + self.per_page
        page_pairs = self.pairs[start:end]

        # Build the embed
        embed = discord.Embed(
            title="📊 Available Trading Pairs",
            description=", ".join(page_pairs),
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages}")
        return embed

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.total_pages - 1:
            self.page += 1
            await interaction.response.edit_message(embed=self.make_embed(), view=self)
