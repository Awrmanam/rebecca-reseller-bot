import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.database.session import init_db


async def _old_database(path, usernames):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            "CREATE TABLE trial_records ("
            "id INTEGER PRIMARY KEY, telegram_id BIGINT NOT NULL UNIQUE, "
            "admin_username VARCHAR(64))"
        )
        for number, username in enumerate(usernames, 1):
            await connection.exec_driver_sql(
                "INSERT INTO trial_records(id, telegram_id, admin_username) VALUES (?, ?, ?)",
                (number, number, username),
            )
    return engine


@pytest.mark.asyncio
async def test_init_db_upgrades_old_trial_table_with_idempotent_unique_index(tmp_path):
    engine = await _old_database(tmp_path / "old.db", ["trial_one", None])
    try:
        await init_db(engine)
        await init_db(engine)
        async with engine.connect() as connection:
            indexes = (await connection.exec_driver_sql("PRAGMA index_list(trial_records)")).all()
            unique = [row for row in indexes if row[2]]
            assert unique
            with pytest.raises(IntegrityError):
                await connection.exec_driver_sql(
                    "INSERT INTO trial_records(id, telegram_id, admin_username) "
                    "VALUES (3, 3, 'trial_one')"
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_init_db_fails_closed_without_rewriting_duplicate_rows(tmp_path):
    engine = await _old_database(tmp_path / "duplicates.db", ["same_name", "same_name"])
    try:
        with pytest.raises(RuntimeError, match="duplicate existing values"):
            await init_db(engine)
        async with engine.connect() as connection:
            count = await connection.scalar(text(
                "SELECT COUNT(*) FROM trial_records WHERE admin_username='same_name'"
            ))
            assert count == 2
    finally:
        await engine.dispose()
