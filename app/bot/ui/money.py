from __future__ import annotations

from decimal import Decimal


def toman_to_rial(value: Decimal | int | str) -> Decimal:
    """Convert a legacy persisted toman value at the presentation boundary."""
    return Decimal(str(value)) * 10


def format_rial(value_toman: Decimal | int | str) -> str:
    """Format a legacy toman value as a customer-visible rial amount."""
    rial = toman_to_rial(value_toman)
    return f"{rial:,.0f} ریال"


def rial_digits(value_toman: Decimal | int | str) -> str:
    """Return exact ASCII rial digits for Telegram CopyTextButton."""
    return f"{toman_to_rial(value_toman):.0f}"


def rial_to_toman(value: str | int | Decimal) -> Decimal:
    """Validate owner panel rial input and convert it for legacy storage."""
    try:
        rial = Decimal(str(value).strip())
    except Exception as exc:
        raise ValueError("قیمت ریالی نامعتبر است.") from exc
    if rial <= 0 or rial != rial.to_integral_value():
        raise ValueError("قیمت باید یک عدد صحیح مثبت به ریال باشد.")
    if int(rial) % 10:
        raise ValueError("قیمت ریالی باید بر 10 بخش‌پذیر باشد.")
    return rial / 10

