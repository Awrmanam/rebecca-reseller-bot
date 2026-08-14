from __future__ import annotations

import logging
from typing import Iterable

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import WarningEvent

log = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, bot: Bot | None, owner_ids: Iterable[int]):
        self.bot = bot
        self.owner_ids = tuple(owner_ids)

    async def send(self, chat_id: int, text: str) -> bool:
        if self.bot is None:
            return False
        try:
            await self.bot.send_message(chat_id, text)
            return True
        except Exception as exc:
            log.warning("telegram notification failed: %s", type(exc).__name__)
            return False

    async def owners(self, text: str) -> None:
        for owner_id in self.owner_ids:
            await self.send(owner_id, text)

    async def once(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        target_type: str,
        target: str,
        kind: str,
        entitlement_key: str,
        text: str,
    ) -> bool:
        exists = await session.scalar(
            select(WarningEvent.id).where(
                WarningEvent.target_type == target_type,
                WarningEvent.target_identifier == target,
                WarningEvent.kind == kind,
                WarningEvent.entitlement_key == entitlement_key,
            )
        )
        if exists:
            return False
        if await self.send(chat_id, text):
            session.add(
                WarningEvent(
                    target_type=target_type,
                    target_identifier=target,
                    kind=kind,
                    entitlement_key=entitlement_key,
                )
            )
            return True
        return False
