"""
Persistent View classes for the main panel buttons.
Registered in setup_hook() with fixed custom_ids so they survive restarts.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config import BOT_NAME
from utils.base_view import SnagView
from utils.embeds import add_invite_branding


def _build_how_to_use_embeds() -> list[discord.Embed]:
    """Build the static 7-page How to Use guide. Each page gets invite branding."""
    pages: list[discord.Embed] = []

    # ── Page 1: What is Snag? ─────────────────────────────────────────────────
    e = discord.Embed(
        title=f"❓ What is {BOT_NAME}? (1/7)",
        description=(
            f"**{BOT_NAME}** is a cross-server Minecraft trading marketplace — "
            "a bot-managed way to buy, sell, and trade items, services, and builds "
            "with players across **every Discord server that has Snag installed**.\n\n"
            "You don't have to be in the same server as the other trader. "
            f"{BOT_NAME} handles the introduction: it DMs both of you a private deal "
            "panel where you can chat, confirm the trade, and rate each other — all "
            "without sharing any personal contact info.\n\n"
            "Your profile (IGN, star rating, deal history) follows you everywhere "
            f"{BOT_NAME} is installed, so your reputation compounds across the whole network."
        ),
        color=0x5865F2,
    )
    pages.append(add_invite_branding(e))

    # ── Page 2: Creating a listing ────────────────────────────────────────────
    e = discord.Embed(
        title="✏️ How to Create a Listing (2/7)",
        description=(
            "1. Click **✏️ Create Listing** on the marketplace panel.\n"
            "2. Choose **Server-only** (visible only in this server) or "
            "**Global** (visible in every server that has Snag).\n"
            "3. Pick a **Category** — Items, Bases, Service, Ranks/Currency, Builds, Farms, or Other.\n"
            "4. Pick which **Minecraft server** the listing is for (or 'Any').\n"
            "5. Choose **Buying** (you want something) or **Selling** (you're offering something).\n"
            "6. Choose **Direct Sale** (fixed price, deal starts instantly) "
            "or **Auction** (highest bidder wins when time runs out).\n"
            "7. Fill in the **title, description, and price** form.\n"
            "8. Review the preview embed — check everything looks right.\n"
            "9. Click **✅ Confirm & Post** — your listing is live!\n\n"
            "You can have up to **10 active listings** at once. "
            "Listings expire after **14 days** — you'll get a DM reminder before that happens so you can renew."
        ),
        color=0x5865F2,
    )
    pages.append(add_invite_branding(e))

    # ── Page 3: Browsing & searching ──────────────────────────────────────────
    e = discord.Embed(
        title="🔍 How to Browse & Search (3/7)",
        description=(
            "1. Click **🔍 Check Listings** on the panel.\n"
            "2. Use the **dropdown menus** to filter by MC server, category, buy/sell type, and format.\n"
            "3. Click **🏠 Server Listings** (this server only) or **🌐 Global Listings** (everywhere) to run the search.\n"
            "4. Use **💰 Price: Low→High** or **💰 Price: High→Low** to sort results by price.\n"
            "5. Use **◀ Prev** / **Next ▶** to page through results.\n"
            "6. On any listing you like, click **🤝 Start Deal** (direct sale) or **🔨 Place Bid** (auction).\n\n"
            "**📋 My Listings** shows your own active listings with **✏️ Edit** and **🗑️ Cancel** buttons.\n\n"
            "You can also type a keyword directly into the 🔍 Search field to find specific items."
        ),
        color=0x5865F2,
    )
    pages.append(add_invite_branding(e))

    # ── Page 4: How deals work ────────────────────────────────────────────────
    e = discord.Embed(
        title="🤝 How Deals Work (4/7)",
        description=(
            "When you click **Start Deal**, Snag DMs **both of you** a private deal panel.\n\n"
            "**💬 Just type normally in that DM** — Snag relays your message to the other "
            "trader automatically. No commands needed. It's like a private chat room just for the two of you "
            "(\"meet at spawn\", \"sent it — check your chest\", etc.).\n\n"
            "**To complete a trade:**\n"
            "Both parties click **✅ Mark Complete** once you've received your end of the deal. "
            "The trade finishes once **both sides confirm**. When only one side confirms, "
            "the other party gets a nudge DM reminding them it's their turn.\n\n"
            "**Other actions in the deal panel:**\n"
            "• **❌ Cancel Deal** — ends the deal early and reopens the listing for others.\n"
            "• **🚨 Report Issue** — flags a problem to this server's moderators.\n\n"
            "**⚠️ Limits to know:**\n"
            "• You can only be in **one active deal at a time** across the whole bot.\n"
            "• A deal with no activity for **48 hours** auto-closes."
        ),
        color=0x5865F2,
    )
    pages.append(add_invite_branding(e))

    # ── Page 5: Reviews ───────────────────────────────────────────────────────
    e = discord.Embed(
        title="⭐ Reviews (5/7)",
        description=(
            "After a deal completes, Snag DMs **both traders** a review prompt asking "
            "you to rate the other person **1–5 stars** with an optional comment.\n\n"
            "**Important:** You **cannot start a new deal** until you've rated your last completed one. "
            "This keeps ratings honest — everyone has skin in the game, so reviews actually mean something.\n\n"
            "Your star rating and review count show up on your listings and profile, "
            "so building a good reputation directly helps you get more trades and higher bids.\n\n"
            "A ⭐ trusted-seller badge appears next to your listings once you reach "
            "10 completed deals with a 4.5+ average rating."
        ),
        color=0x5865F2,
    )
    pages.append(add_invite_branding(e))

    # ── Page 6: Auctions & bidding ────────────────────────────────────────────
    e = discord.Embed(
        title="🔨 Auctions & Bidding (6/7)",
        description=(
            "Auction listings appear in **Check Listings** just like direct sales — "
            "click **🔨 Place Bid** to open the bid form.\n\n"
            "**Bidding rules:**\n"
            "• Your bid must exceed the current highest bid by at least **5%** (or 1 unit minimum).\n"
            "• Bidding in the last **2 minutes** automatically extends the auction by 2 minutes "
            "to prevent last-second sniping (max 5 extensions).\n\n"
            "**How the auction ends:**\n"
            "• When time runs out, the highest valid bidder automatically gets a deal DM — "
            "no extra action needed from you.\n"
            "• If you're outbid, you'll receive a DM notification so you can bid again.\n"
            "• If an auction ends with no bids, the seller is notified and the listing expires."
        ),
        color=0x5865F2,
    )
    pages.append(add_invite_branding(e))

    # ── Page 7: Staying safe ──────────────────────────────────────────────────
    e = discord.Embed(
        title="🛡️ Staying Safe (7/7)",
        description=(
            f"**{BOT_NAME} doesn't guarantee any trade.** It's a marketplace, not an escrow service.\n\n"
            "**Rules to trade by:**\n"
            "• **Never send real-world payment info** (PayPal, credit cards, Venmo, etc.) in a deal DM — "
            "in-game currency and items only.\n"
            "• If something feels off, click **🚨 Report Issue** in the deal panel — "
            "that goes straight to this server's moderators.\n"
            "• Set your **🎮 My IGN** so your trade partner knows who to look for in-game.\n"
            "• Check a trader's ⭐ rating and review count before accepting a deal.\n\n"
            f"*{BOT_NAME} is a cross-server bot — you can use it in any server it's installed in, "
            "and your profile follows you everywhere.*"
        ),
        color=0x5865F2,
    )
    pages.append(add_invite_branding(e))

    return pages


class MainPanelView(SnagView):
    """
    The permanent 5-button panel.  Registered on setup_hook with timeout=None
    so it persists across restarts.  Discord allows 5 buttons per row — all 5 fit.
    """

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✏️ Create Listing",
        style=discord.ButtonStyle.primary,
        custom_id="panel:create_listing",
    )
    async def create_listing(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        from cogs.listings import start_create_listing_wizard
        await start_create_listing_wizard(interaction)

    @discord.ui.button(
        label="🔍 Check Listings",
        style=discord.ButtonStyle.secondary,
        custom_id="panel:check_listings",
    )
    async def check_listings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        from cogs.listings import start_check_listings
        await start_check_listings(interaction)

    @discord.ui.button(
        label="📋 My Listings",
        style=discord.ButtonStyle.secondary,
        custom_id="panel:my_listings",
    )
    async def my_listings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        from cogs.listings import start_my_listings
        await start_my_listings(interaction)

    @discord.ui.button(
        label="🎮 My IGN",
        style=discord.ButtonStyle.secondary,
        custom_id="panel:my_ign",
    )
    async def my_ign(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.listings import IGNModal
        await interaction.response.send_modal(IGNModal())

    @discord.ui.button(
        label="❓ How to Use",
        style=discord.ButtonStyle.secondary,
        custom_id="panel:how_to_use",
    )
    async def how_to_use_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        from utils.pagination import PaginatorView
        embeds = _build_how_to_use_embeds()
        paginator = PaginatorView(embeds)
        await interaction.followup.send(embed=paginator.current_page_embed(), view=paginator, ephemeral=True)


class PanelViews(commands.Cog):
    """Cog that registers persistent views and the /help command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description=f"Show the Snag how-to-use guide")
    async def help_command(self, interaction: discord.Interaction):
        """Paginated how-to-use guide — same 7 pages as the ❓ How to Use panel button."""
        await interaction.response.defer(ephemeral=True)
        from utils.pagination import PaginatorView
        embeds = _build_how_to_use_embeds()
        paginator = PaginatorView(embeds)
        await interaction.followup.send(embed=paginator.current_page_embed(), view=paginator, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PanelViews(bot))
    # Views are registered in main.py's setup_hook
