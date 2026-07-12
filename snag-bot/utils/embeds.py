"""
Shared embed builder utilities.
Every embed that goes to a user or channel must call `add_invite_branding()`.
"""

import discord
from config import BOT_NAME, INVITE_URL, DEFAULT_EMBED_COLOR, TRUSTED_BADGE_MIN_COMPLETED_DEALS, TRUSTED_BADGE_MIN_AVG_RATING


def add_invite_branding(embed: discord.Embed) -> discord.Embed:
    """
    Append the invite branding line to an embed's description.
    Must be in description (not footer) so the markdown link renders as blue clickable text.
    """
    branding = f"\n\n💫 *Enjoying {BOT_NAME}? [Invite it to your server!]({INVITE_URL})*"
    if embed.description:
        embed.description += branding
    else:
        embed.description = branding
    return embed


def build_base_embed(
    title: str,
    description: str = "",
    color: int | None = None,
    add_branding: bool = True,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color or DEFAULT_EMBED_COLOR,
    )
    if add_branding:
        embed = add_invite_branding(embed)
    return embed


def build_error_embed(message: str) -> discord.Embed:
    return discord.Embed(
        title="❌ Error",
        description=message,
        color=discord.Color.red(),
    )


def build_success_embed(message: str) -> discord.Embed:
    return discord.Embed(
        title="✅ Success",
        description=message,
        color=discord.Color.green(),
    )


def build_listing_embed(listing, seller_profile=None, guild_color: int | None = None) -> discord.Embed:
    """Build a full listing embed from a Listing ORM object."""
    color = guild_color or DEFAULT_EMBED_COLOR

    scope_label = "🌐 Global" if listing.scope == "global" else "🏠 Server"
    type_label = "🛒 Buying" if listing.listing_type == "buying" else "💰 Selling"
    format_label = "🔨 Auction" if listing.format == "auction" else "🤝 Direct Sale"

    # Trusted badge (computed at render time, never stored)
    badge = ""
    if seller_profile:
        completed = getattr(seller_profile, "_completed_deals", 0)
        avg = (
            seller_profile.global_rating_sum / seller_profile.global_rating_count
            if seller_profile.global_rating_count > 0
            else 0.0
        )
        if completed >= TRUSTED_BADGE_MIN_COMPLETED_DEALS and avg >= TRUSTED_BADGE_MIN_AVG_RATING:
            badge = " ⭐"

    title = f"{listing.title}{badge}"
    desc = listing.description

    embed = discord.Embed(title=title, description=desc, color=color)

    embed.add_field(name="Type", value=f"{type_label} • {format_label}", inline=True)
    embed.add_field(name="Scope", value=scope_label, inline=True)
    embed.add_field(name="Category", value=listing.category, inline=True)

    price_str = (
        f"{listing.price} {listing.currency_label}" if listing.price is not None else "N/A"
    )
    if listing.format == "auction" and listing.highest_bid is not None:
        price_str = f"{listing.highest_bid} {listing.currency_label} *(current bid)*"
    embed.add_field(name="Price / Starting Bid", value=price_str, inline=True)

    if listing.mc_server_tag:
        embed.add_field(name="MC Server", value=listing.mc_server_tag, inline=True)

    if seller_profile:
        ign_text = f"{seller_profile.ign} *(self-reported)*" if seller_profile.ign else "*not set*"
        rating_text = (
            f"⭐ {seller_profile.global_rating_sum / seller_profile.global_rating_count:.1f}"
            f" ({seller_profile.global_rating_count} reviews)"
            if seller_profile.global_rating_count > 0
            else "No reviews yet"
        )
        embed.add_field(name="Seller IGN", value=ign_text, inline=True)
        embed.add_field(name="Rating", value=rating_text, inline=True)

    if listing.format == "auction" and listing.auction_end_at:
        ts = int(listing.auction_end_at.timestamp())
        embed.add_field(name="Auction Ends", value=f"<t:{ts}:R>", inline=True)

    if listing.image_url:
        embed.set_image(url=listing.image_url)

    embed.set_footer(text=f"Listing #{listing.listing_id} • {listing.status.title()}")

    add_invite_branding(embed)
    return embed


def build_deal_panel_embed(deal, listing, color: int | None = None) -> discord.Embed:
    """Build the persistent deal DM panel embed."""
    embed = discord.Embed(
        title=f"🤝 Deal #{deal.deal_id} — {listing.title}",
        description=(
            f"You are in an active deal for **{listing.title}**.\n"
            f"Use the buttons below to manage the deal.\n\n"
            f"⚠️ **{BOT_NAME} does not guarantee any transaction — never send "
            f"real-world payment info, and use Report Issue if something feels wrong.**"
        ),
        color=color or DEFAULT_EMBED_COLOR,
    )
    embed.add_field(name="Status", value=deal.status.title(), inline=True)
    embed.add_field(name="Listing ID", value=str(listing.listing_id), inline=True)
    add_invite_branding(embed)
    return embed


def build_profile_embed(profile, user: discord.User | None = None, color: int | None = None) -> discord.Embed:
    """Build a user profile embed."""
    ign = f"{profile.ign} *(self-reported)*" if profile.ign else "*not set*"
    avg = (
        f"{profile.global_rating_sum / profile.global_rating_count:.1f}"
        if profile.global_rating_count > 0
        else "No reviews"
    )
    embed = discord.Embed(
        title=f"👤 {user.display_name if user else f'User {profile.user_id}'}",
        color=color or DEFAULT_EMBED_COLOR,
    )
    embed.add_field(name="IGN", value=ign, inline=True)
    embed.add_field(name="Avg Rating", value=avg, inline=True)
    embed.add_field(name="Review Count", value=str(profile.global_rating_count), inline=True)
    embed.add_field(name="Timeout Count", value=str(profile.timeout_count), inline=True)
    if profile.is_banned:
        embed.add_field(name="⛔ Global Ban", value=profile.ban_reason or "No reason given", inline=False)
    add_invite_branding(embed)
    return embed
