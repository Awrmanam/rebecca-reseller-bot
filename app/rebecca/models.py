from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


def normalize_status(value: str) -> str:
    normalized = value.lower().strip()
    if normalized in {"active", "enabled"}:
        return "active"
    if normalized in {"disabled", "inactive"}:
        return "disabled"
    return normalized


def parse_expire(value: Any) -> datetime | None:
    """Normalize Rebecca's Unix timestamp (or an already parsed ISO value)."""
    if value in (None, 0, "", "0"):
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)) or str(value).isdigit():
        return datetime.fromtimestamp(int(value), UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def serialize_expire(value: datetime | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    aware = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return int(aware.timestamp())


class Admin(BaseModel):
    username: str
    role: str
    status: str = "active"
    expire: datetime | None = None
    data_limit: int | None = None
    data_limit_unlimited: bool = False
    used_traffic: int = 0
    services: list[int | str] = Field(default_factory=list)
    users_limit: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expire", mode="before")
    @classmethod
    def normalize_expire(cls, value: Any) -> datetime | None:
        return parse_expire(value)

    @classmethod
    def from_rebecca(cls, payload: dict[str, Any]) -> Admin:
        data = dict(payload)
        # Zero/null is potentially unlimited across Rebecca versions. Treat it
        # explicitly as unlimited/ambiguous so lifecycle code fails closed and
        # never authorizes an automatic deletion from that value alone.
        raw_limit = data.get("data_limit")
        data["data_limit_unlimited"] = raw_limit in (None, 0)
        # Modern Rebecca reports aggregate reseller consumption as users_usage.
        data["used_traffic"] = int(data.get("users_usage") or data.get("used_traffic") or 0)
        data["raw"] = payload
        return cls.model_validate(data)


class User(BaseModel):
    username: str
    admin_username: str | None = None
    admin_id: int | None = None
    status: str = "active"
    expire: datetime | None = None
    data_limit: int | None = None
    data_limit_unlimited: bool = False
    used_traffic: int = 0
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expire", mode="before")
    @classmethod
    def normalize_expire(cls, value: Any) -> datetime | None:
        return parse_expire(value)

    @classmethod
    def from_rebecca(cls, payload: dict[str, Any]) -> User:
        data = dict(payload)
        raw_limit = data.get("data_limit")
        data["data_limit_unlimited"] = raw_limit in (None, 0)
        data["used_traffic"] = int(data.get("used_traffic") or data.get("usage") or 0)
        data["raw"] = payload
        return cls.model_validate(data)
