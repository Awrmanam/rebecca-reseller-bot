from __future__ import annotations

import aiogram.types as telegram_types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.ui.money import rial_digits


def card_payment_keyboard(amount_toman, card_number: str | None, product_id: int):
    """Native copy controls; amounts and card data never enter callback_data."""
    copy_type = getattr(telegram_types, "CopyTextButton", None)
    rows = []
    if copy_type is not None:
        rows.append([InlineKeyboardButton(text="📋 کپی مبلغ", copy_text=copy_type(text=rial_digits(amount_toman)))])
        if card_number:
            rows.append([InlineKeyboardButton(text="📋 کپی شماره کارت", copy_text=copy_type(text=str(card_number)))])
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data=f"buy:{product_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
