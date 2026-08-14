from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database.models import Setting


class RuntimeSettingsService:
    """Validated non-secret DB overrides with environment fallbacks."""

    EDITABLE = {
        "user_delete_grace_hours",
        "time_warning_thresholds",
        "traffic_warning_thresholds",
    }

    def __init__(self, defaults: Settings):
        self.defaults = defaults

    async def _value(self, session: AsyncSession, key: str) -> Any:
        value = await session.scalar(select(Setting.value).where(Setting.key == key))
        return getattr(self.defaults, key) if value is None else value

    async def grace_hours(self, session: AsyncSession) -> int:
        value = int(await self._value(session, "user_delete_grace_hours"))
        if not 1 <= value <= 24 * 30:
            raise ValueError("user_delete_grace_hours must be between 1 and 720")
        return value

    async def time_thresholds(self, session: AsyncSession) -> tuple[int, ...]:
        return self._thresholds(await self._value(session, "time_warning_thresholds"))

    async def traffic_thresholds(self, session: AsyncSession) -> tuple[int, ...]:
        values = self._thresholds(await self._value(session, "traffic_warning_thresholds"))
        if any(item > 100 for item in values):
            raise ValueError("traffic thresholds cannot exceed 100")
        return values

    @staticmethod
    def _thresholds(value: Any) -> tuple[int, ...]:
        if isinstance(value, str):
            value = value.split(",")
        result = tuple(sorted({int(item) for item in value}, reverse=True))
        if not result or any(item <= 0 for item in result):
            raise ValueError("thresholds must be positive")
        return result
