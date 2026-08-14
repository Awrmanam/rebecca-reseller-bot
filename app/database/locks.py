from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LifecycleLock


async def acquire(
    session: AsyncSession, key: str, *, ttl_seconds: int = 300
) -> bool:
    now = datetime.now(UTC)
    await session.execute(
        delete(LifecycleLock).where(
            LifecycleLock.key == key, LifecycleLock.expires_at <= now
        )
    )
    session.add(
        LifecycleLock(
            key=key,
            acquired_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return False
    return True


async def release(session: AsyncSession, key: str) -> None:
    await session.execute(delete(LifecycleLock).where(LifecycleLock.key == key))


@asynccontextmanager
async def locked(session: AsyncSession, key: str, *, ttl_seconds: int = 300):
    acquired = await acquire(session, key, ttl_seconds=ttl_seconds)
    try:
        yield acquired
    finally:
        if acquired:
            await release(session, key)
