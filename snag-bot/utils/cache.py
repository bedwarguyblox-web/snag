"""
In-memory guild_config cache.
GuildConfig rows change rarely — cache them instead of hitting Postgres on every panel click.
Invalidated whenever /setup writes a new value (section 7).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database.models import GuildConfig

logger = logging.getLogger(__name__)

_cache: dict[int, "GuildConfig"] = {}


def get(guild_id: int) -> "GuildConfig | None":
    return _cache.get(guild_id)


def set(config: "GuildConfig") -> None:
    _cache[config.guild_id] = config


def invalidate(guild_id: int) -> None:
    _cache.pop(guild_id, None)
    logger.debug("Guild config cache invalidated for guild %d", guild_id)


def clear_all() -> None:
    _cache.clear()
