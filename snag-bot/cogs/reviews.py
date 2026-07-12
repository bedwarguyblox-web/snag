"""
Review modal and mandatory review gate.
Same-pair review cap prevents rating-farming between colluding accounts.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from config import REVIEW_COMMENT_MAX_LEN, SAME_PAIR_REVIEW_CAP
from database.engine import AsyncSessionLocal
from database.models import Review, UserProfile, Deal
from utils.checks import get_or_create_profile
from utils.embeds import build_error_embed, build_success_embed

logger = logging.getLogger(__name__)


class ReviewModal(discord.ui.Modal, title="Rate Your Trade"):
    rating_input = discord.ui.TextInput(
        label="Rating (1–5 stars)",
        placeholder="Enter a number from 1 to 5",
        max_length=1,
    )
    comment_input = discord.ui.TextInput(
        label="Comment (optional)",
        style=discord.TextStyle.paragraph,
        max_length=REVIEW_COMMENT_MAX_LEN,
        required=False,
        placeholder="How did the trade go?",
    )

    def __init__(self, deal_id: int, reviewer_id: int, reviewee_id: int):
        super().__init__()
        self._deal_id = deal_id
        self._reviewer_id = reviewer_id
        self._reviewee_id = reviewee_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Validate rating
        try:
            rating = int(self.rating_input.value.strip())
            if not (1 <= rating <= 5):
                raise ValueError
        except ValueError:
            await interaction.followup.send(
                embed=build_error_embed("Rating must be a whole number from 1 to 5."),
                ephemeral=True,
            )
            return

        comment = self.comment_input.value.strip() or None

        # Verify the deal actually completed and the reviewer is a party
        async with AsyncSessionLocal() as session:
            r = await session.execute(select(Deal).where(Deal.deal_id == self._deal_id))
            deal = r.scalar_one_or_none()

        if not deal or deal.status != "completed":
            await interaction.followup.send(
                embed=build_error_embed("This deal is not completed or doesn't exist."),
                ephemeral=True,
            )
            return

        if interaction.user.id != self._reviewer_id:
            await interaction.followup.send(
                embed=build_error_embed("This review prompt is not for you."),
                ephemeral=True,
            )
            return

        # Check same-pair review cap
        async with AsyncSessionLocal() as session:
            pair_count_result = await session.execute(
                select(func.count()).where(
                    Review.reviewer_id == self._reviewer_id,
                    Review.reviewee_id == self._reviewee_id,
                )
            )
            pair_count = pair_count_result.scalar_one()

        counts_toward_avg = pair_count < SAME_PAIR_REVIEW_CAP

        async with AsyncSessionLocal() as session:
            async with session.begin():
                # Check for existing review on this deal (unique constraint guard)
                existing = await session.execute(
                    select(Review).where(
                        Review.deal_id == self._deal_id,
                        Review.reviewer_id == self._reviewer_id,
                    )
                )
                if existing.scalar_one_or_none():
                    await interaction.followup.send(
                        embed=build_error_embed("You've already reviewed this deal."),
                        ephemeral=True,
                    )
                    return

                review = Review(
                    deal_id=self._deal_id,
                    reviewer_id=self._reviewer_id,
                    reviewee_id=self._reviewee_id,
                    rating=rating,
                    comment=comment,
                    counts_toward_average=counts_toward_avg,
                )
                session.add(review)

                if counts_toward_avg:
                    # Update reviewee's aggregate
                    reviewee_profile = await get_or_create_profile(session, self._reviewee_id)
                    reviewee_profile.global_rating_sum += rating
                    reviewee_profile.global_rating_count += 1

                # Clear pending_review_deal_id for the reviewer (only if it matches this deal)
                reviewer_profile = await get_or_create_profile(session, self._reviewer_id)
                if reviewer_profile.pending_review_deal_id == self._deal_id:
                    reviewer_profile.pending_review_deal_id = None

        cap_note = ""
        if not counts_toward_avg:
            cap_note = "\n*(Note: This review was recorded but does not affect the trader's average — same-pair review cap reached.)*"

        await interaction.followup.send(
            embed=build_success_embed(
                f"Review submitted! You gave **{rating}★**{(' — ' + comment) if comment else ''}.{cap_note}"
            ),
            ephemeral=True,
        )


class Reviews(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Reviews(bot))
