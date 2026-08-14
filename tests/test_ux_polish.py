from decimal import Decimal

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.bot.ui.money import format_rial, rial_digits, rial_to_toman, toman_to_rial
from app.bot.ui.navigation import safe_edit
from app.bot.ui.render import card_payment_keyboard
from app.bot.ui.status import status_text


def test_rial_boundary_and_owner_input():
    assert toman_to_rial(Decimal("1490000")) == Decimal("14900000")
    assert format_rial(1490000) == "14,900,000 ریال"
    assert rial_digits(1490000) == "14900000"
    assert rial_to_toman("14900000") == Decimal("1490000")
    with pytest.raises(ValueError):
        rial_to_toman("14900001")


def test_card_copy_uses_plain_rial_digits_not_callback_data():
    keyboard = card_payment_keyboard(1490000, "6037991234567890", 7)
    amount = keyboard.inline_keyboard[0][0]
    assert amount.copy_text.text == "14900000"
    assert amount.callback_data is None
    assert all("14900000" not in (button.callback_data or "") for row in keyboard.inline_keyboard for button in row)


def test_human_statuses():
    assert status_text("PROVISIONING") == "🟡 در حال ساخت"
    assert status_text("WAITING_RECEIPT") == "🟡 منتظر بررسی رسید"
    assert status_text("APPLIED") == "🟢 تکمیل‌شده"


@pytest.mark.asyncio
async def test_safe_edit_ignores_only_message_not_modified():
    class Message:
        async def edit_text(self, *args, **kwargs):
            raise TelegramBadRequest(method="editMessageText", message="Bad Request: message is not modified")
    message = Message()
    assert await safe_edit(message, "same") is message

    class Broken:
        async def edit_text(self, *args, **kwargs):
            raise TelegramBadRequest(method="editMessageText", message="Bad Request: message can't be edited")
    with pytest.raises(TelegramBadRequest):
        await safe_edit(Broken(), "new")
