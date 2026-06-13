from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal


PriceUnit = Literal["auto", "euros", "thousands", "millions"]


def normalize_price_millions(raw_value: object, unit: PriceUnit = "auto") -> float:
    """Return a fixed price in millions without silently accepting invalid values."""
    if raw_value is None or isinstance(raw_value, bool):
        raise ValueError("Price is required and must be numeric.")

    value_text = str(raw_value).strip().replace(" ", "")
    if not value_text:
        raise ValueError("Price is required and must be numeric.")

    # JSON numbers should be preferred. This also accepts common CSV decimal commas.
    if "," in value_text and "." not in value_text:
        value_text = value_text.replace(",", ".")
    else:
        value_text = value_text.replace(",", "")

    try:
        value = Decimal(value_text)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid price: {raw_value!r}.") from exc

    if not value.is_finite() or value <= 0:
        raise ValueError("Price must be a positive finite number.")

    if unit == "euros":
        millions = value / Decimal("1000000")
    elif unit == "thousands":
        millions = value / Decimal("1000")
    elif unit == "millions":
        millions = value
    elif unit == "auto":
        if value >= Decimal("1000000"):
            millions = value / Decimal("1000000")
        elif value >= Decimal("1000"):
            millions = value / Decimal("1000")
        else:
            millions = value
    else:
        raise ValueError(f"Unsupported price unit: {unit!r}.")

    return round(float(millions), 6)


def fixed_price_from_record(record: dict, unit: PriceUnit = "auto") -> tuple[float, str]:
    """Extract only fields that explicitly represent a fixed fantasy price."""
    fixed_fields = (
        "fantasyPrice",
        "fixedPrice",
        "fixed_price",
        "player.fantasyPrice",
        "player.fixedPrice",
    )

    for field in fixed_fields:
        current: object = record
        for part in field.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if current not in (None, "", 0, 0.0):
            return normalize_price_millions(current, unit), field

    raise ValueError("No explicit fixed-price field was found.")
