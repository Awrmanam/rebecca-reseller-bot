import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.database.models import Base, Setting
from app.database.settings import RuntimeSettingsService


@pytest.mark.asyncio
async def test_database_grace_override_changes_effective_value():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = RuntimeSettingsService(Settings(user_delete_grace_hours=72))
    async with sessions() as session, session.begin():
        assert await service.grace_hours(session) == 72
        session.add(Setting(key="user_delete_grace_hours", value=24))
    async with sessions() as session:
        assert await service.grace_hours(session) == 24
