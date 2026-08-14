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
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
