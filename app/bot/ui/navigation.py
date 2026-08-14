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


async def mark_receipt_processed(message, label: str):
    """Finalize a receipt media caption and remove obsolete action buttons."""
    caption = getattr(message, "caption", None)
    if caption is not None:
        updated = caption if label in caption else f"{caption}\n\n{label}"
        try:
            return await message.edit_caption(caption=updated, reply_markup=None)
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return message
            raise
    text = getattr(message, "text", None) or label
    updated = text if label in text else f"{text}\n\n{label}"
    return await safe_edit(message, updated, reply_markup=None)
