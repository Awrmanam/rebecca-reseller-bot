from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database.models import Setting


class RuntimeSettingsService:
    """Validated non-secret DB overrides with environment fallbacks."""

    EDITABLE = {
        "operations_mode",
        "user_delete_grace_hours",
        "time_warning_thresholds",
        "traffic_warning_thresholds",
        "customer_panel_url",
        "trial_traffic_gb",
        "trial_duration_hours",
    }

    def __init__(self, defaults: Settings):
        self.defaults = defaults

    async def _value(self, session: AsyncSession, key: str) -> Any:
        value = await session.scalar(select(Setting.value).where(Setting.key == key))
        return getattr(self.defaults, key) if value is None else value

    async def operations_mode(self, session: AsyncSession) -> str:
        value = await session.scalar(
            select(Setting.value).where(Setting.key == "operations_mode")
        )
        if value is None:
            return "dry_run" if self.defaults.dry_run else "live"
        if value not in {"dry_run", "live"}:
            raise ValueError("invalid operations_mode")
        return value

    async def is_dry_run(self, session: AsyncSession) -> bool:
        return await self.operations_mode(session) == "dry_run"

    async def set_operations_mode(self, session: AsyncSession, mode: str) -> None:
        if mode not in {"dry_run", "live"}:
            raise ValueError("invalid operations_mode")
        row = await session.get(Setting, "operations_mode")
        if row:
            row.value = mode
        else:
            session.add(Setting(key="operations_mode", value=mode))

    async def grace_hours(self, session: AsyncSession) -> int:
        value = int(await self._value(session, "user_delete_grace_hours"))
        if not 1 <= value <= 24 * 30:
            raise ValueError("user_delete_grace_hours must be between 1 and 720")
        return value

    async def trial_traffic_gb(self, session: AsyncSession) -> int:
        value = int(await self._value(session, "trial_traffic_gb"))
        if value <= 0:
            raise ValueError("trial_traffic_gb must be positive")
        return value

    async def trial_duration_hours(self, session: AsyncSession) -> int:
        value = int(await self._value(session, "trial_duration_hours"))
        if value <= 0:
            raise ValueError("trial_duration_hours must be positive")
        return value

    async def customer_panel_url(self, session: AsyncSession) -> str | None:
        """Read the latest persisted panel URL; this deliberately has no cache."""
        value = await session.scalar(
            select(Setting.value).where(Setting.key == "customer_panel_url")
        )
        if value is None:
            value = getattr(self.defaults, "customer_panel_url", None)
        if value is None or not str(value).strip():
            return None
        value = str(value).strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError("customer_panel_url must use http:// or https://")
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
