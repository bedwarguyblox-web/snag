"""
Shared input-parsing helpers.
"""

from __future__ import annotations

import re

# Suffix multipliers for shorthand amounts like "10k", "2.5m", "1b".
_SUFFIX_MULTIPLIERS = {
    "k": 1_000,
    "m": 1_000_000,
    "b": 1_000_000_000,
}

_AMOUNT_RE = re.compile(r"^([0-9]*\.?[0-9]+)\s*([kmb]?)$", re.IGNORECASE)


def parse_amount(raw: str) -> float:
    """
    Parse a user-supplied amount string into a float.

    Accepts plain numbers ("500", "12.50") as well as shorthand with a
    trailing letter multiplier: "10k" -> 10000, "2b" -> 2_000_000_000,
    "1.5m" -> 1_500_000. Commas are stripped before parsing.

    Raises ValueError if the string can't be parsed.
    """
    cleaned = raw.strip().replace(",", "")
    if not cleaned:
        raise ValueError("Empty amount")

    match = _AMOUNT_RE.match(cleaned)
    if not match:
        raise ValueError(f"Invalid amount: {raw!r}")

    number_part, suffix = match.groups()
    value = float(number_part)
    if suffix:
        value *= _SUFFIX_MULTIPLIERS[suffix.lower()]
    return value
