from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from .models import Base

def make_engine(url: str) -> AsyncEngine:
    engine=create_async_engine(url)
    if url.startswith("sqlite"):
        @event.listens_for(engine.sync_engine, "connect")
        def pragmas(dbapi_connection, _):
            cursor=dbapi_connection.cursor(); cursor.execute("PRAGMA journal_mode=WAL"); cursor.execute("PRAGMA foreign_keys=ON"); cursor.execute("PRAGMA busy_timeout=5000"); cursor.close()
    return engine
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]: return async_sessionmaker(engine, expire_on_commit=False)
async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.url.drivername.startswith("sqlite"):
            columns = {
                row[1]
                for row in (
                    await conn.exec_driver_sql("PRAGMA table_info(reseller_users)")
                ).all()
            }
            if "disabled_by_own_expiry" not in columns:
                await conn.exec_driver_sql(
                    "ALTER TABLE reseller_users "
                    "ADD COLUMN disabled_by_own_expiry BOOLEAN NOT NULL DEFAULT 0"
                )
            duplicates = (
                await conn.exec_driver_sql(
                    "SELECT admin_username, COUNT(*) FROM trial_records "
                    "WHERE admin_username IS NOT NULL GROUP BY admin_username "
                    "HAVING COUNT(*) > 1 LIMIT 10"
                )
            ).all()
            if duplicates:
                usernames = ", ".join(str(row[0]) for row in duplicates)
                raise RuntimeError(
                    "cannot enforce unique trial admin usernames; "
                    f"duplicate existing values require manual review: {usernames}"
                )
            has_unique_admin_username = False
            for index in (await conn.exec_driver_sql("PRAGMA index_list(trial_records)")).all():
                if not index[2]:
                    continue
                safe_name = str(index[1]).replace('"', '""')
                indexed_columns = [
                    row[2]
                    for row in (
                        await conn.exec_driver_sql(f'PRAGMA index_info("{safe_name}")')
                    ).all()
                ]
                if indexed_columns == ["admin_username"]:
                    has_unique_admin_username = True
                    break
            if not has_unique_admin_username:
                await conn.exec_driver_sql(
                    "CREATE UNIQUE INDEX ux_trial_records_admin_username_not_null "
                    "ON trial_records(admin_username) WHERE admin_username IS NOT NULL"
                )
