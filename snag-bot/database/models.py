"""
All SQLAlchemy ORM models for Snag.
Field names and types are final — every cog depends on this schema.
"""

from datetime import datetime, timezone, timedelta

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _listing_expires() -> datetime:
    from config import LISTING_EXPIRY_DAYS
    return datetime.now(timezone.utc) + timedelta(days=LISTING_EXPIRY_DAYS)


class Base(DeclarativeBase):
    pass


# ─── guild_configs ─────────────────────────────────────────────────────────────
class GuildConfig(Base):
    __tablename__ = "guild_configs"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    embed_color: Mapped[str] = mapped_column(String(7), default="#5865F2")
    allow_global_listings: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_server_listings: Mapped[bool] = mapped_column(Boolean, default=True)
    custom_categories: Mapped[list] = mapped_column(JSON, default=list)
    panel_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    panel_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    admin_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    log_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    global_feed_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


# ─── user_profiles ─────────────────────────────────────────────────────────────
class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ign: Mapped[str | None] = mapped_column(String(32), nullable=True)
    global_rating_sum: Mapped[int] = mapped_column(Integer, default=0)
    global_rating_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_review_deal_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("deals.deal_id"),
        nullable=True,
    )
    timeout_count: Mapped[int] = mapped_column(Integer, default=0)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    ban_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ─── guild_bans ────────────────────────────────────────────────────────────────
class GuildBan(Base):
    __tablename__ = "guild_bans"
    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", name="uq_guild_ban"),
    )

    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guild_configs.guild_id"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    banned_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ─── listings ──────────────────────────────────────────────────────────────────
class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        CheckConstraint("scope IN ('server','global')", name="ck_listing_scope"),
        CheckConstraint(
            "listing_type IN ('buying','selling')", name="ck_listing_type"
        ),
        CheckConstraint(
            "format IN ('direct_sale','auction')", name="ck_listing_format"
        ),
        CheckConstraint(
            "status IN ('active','pending_deal','completed','cancelled','expired')",
            name="ck_listing_status",
        ),
        # Required indexes (section 7)
        Index("ix_listings_status_scope", "status", "scope"),
        Index(
            "ix_listings_filter",
            "mc_server_tag",
            "category",
            "listing_type",
            "format",
        ),
        Index("ix_listings_seller_id", "seller_id"),
    )

    listing_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    seller_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_profiles.user_id")
    )
    origin_guild_id: Mapped[int] = mapped_column(BigInteger)
    scope: Mapped[str] = mapped_column(String(10))
    mc_server_tag: Mapped[str | None] = mapped_column(String(50), nullable=True)
    category: Mapped[str] = mapped_column(String(50))
    listing_type: Mapped[str] = mapped_column(String(10))
    format: Mapped[str] = mapped_column(String(15))
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(1000))
    price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency_label: Mapped[str] = mapped_column(String(30), default="in-game currency")
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(15), default="active")
    highest_bid: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    highest_bidder_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    auction_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    auction_extension_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_listing_expires
    )


# ─── bids ──────────────────────────────────────────────────────────────────────
class Bid(Base):
    __tablename__ = "bids"
    __table_args__ = (
        Index("ix_bids_listing_id", "listing_id"),
    )

    bid_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("listings.listing_id")
    )
    bidder_id: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ─── deals ─────────────────────────────────────────────────────────────────────
class Deal(Base):
    __tablename__ = "deals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','completed','cancelled','expired')",
            name="ck_deal_status",
        ),
        # Section 7 index for the 48h timeout sweep
        Index("ix_deals_status_last_activity", "status", "last_activity_at"),
    )

    deal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("listings.listing_id")
    )
    initiator_id: Mapped[int] = mapped_column(BigInteger)
    seller_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(15), default="active")
    initiator_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    seller_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    dm_message_id_initiator: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    dm_message_id_seller: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    end_reason: Mapped[str | None] = mapped_column(String(25), nullable=True)


# ─── reviews ───────────────────────────────────────────────────────────────────
class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_review_rating"),
        UniqueConstraint("deal_id", "reviewer_id", name="uq_review_per_deal"),
        Index("ix_reviews_reviewee_id", "reviewee_id"),
        Index("ix_reviews_pair", "reviewer_id", "reviewee_id"),
    )

    review_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    deal_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("deals.deal_id"))
    reviewer_id: Mapped[int] = mapped_column(BigInteger)
    reviewee_id: Mapped[int] = mapped_column(BigInteger)
    rating: Mapped[int] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    counts_toward_average: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ─── reports ───────────────────────────────────────────────────────────────────
class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','reviewed','dismissed')", name="ck_report_status"
        ),
        Index("ix_reports_reported_user_id", "reported_user_id"),
    )

    report_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    deal_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("deals.deal_id"), nullable=True
    )
    reporter_id: Mapped[int] = mapped_column(BigInteger)
    reported_user_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(15), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ─── Archive tables (section 7) ───────────────────────────────────────────────
class ListingArchive(Base):
    """Terminal listings older than ARCHIVE_AFTER_DAYS moved here by weekly sweep."""
    __tablename__ = "listings_archive"

    listing_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    seller_id: Mapped[int] = mapped_column(BigInteger)
    origin_guild_id: Mapped[int] = mapped_column(BigInteger)
    scope: Mapped[str] = mapped_column(String(10))
    mc_server_tag: Mapped[str | None] = mapped_column(String(50), nullable=True)
    category: Mapped[str] = mapped_column(String(50))
    listing_type: Mapped[str] = mapped_column(String(10))
    format: Mapped[str] = mapped_column(String(15))
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(1000))
    price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency_label: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(15))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BidArchive(Base):
    """Bids moved alongside their archived listings."""
    __tablename__ = "bids_archive"

    bid_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(BigInteger)
    bidder_id: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
