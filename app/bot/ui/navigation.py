from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest


async def safe_edit(message, text: str, *, reply_markup=None, **kwargs):
    """Edit a bot-owned panel, ignoring only Telegram's harmless no-op error."""
    try:
        return await message.edit_text(text, reply_markup=reply_markup, **kwargs)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return message
        raise


async def edit_or_answer(message, text: str, *, reply_markup=None, **kwargs):
    """Edit callback panels and answer user-originated reply-keyboard messages."""
    sender = getattr(message, "from_user", None)
    if sender is not None and getattr(sender, "is_bot", False):
        return await safe_edit(message, text, reply_markup=reply_markup, **kwargs)
    return await message.answer(text, reply_markup=reply_markup, **kwargs)
