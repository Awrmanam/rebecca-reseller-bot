import asyncio, logging
from aiogram import Bot, Dispatcher
from fastapi import FastAPI
from uvicorn import Config, Server
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.bot.handlers.common import router as bot_router
from app.bot.handlers.owner import router as owner_router
from app.config import get_settings
from app.database.models import CapabilitySnapshot
from app.database.session import init_db, make_engine, session_factory
from app.payments.webhook import router as payment_router
from app.rebecca.client import HTTPRebeccaClient
from app.notifications.service import NotificationService
from app.scheduler.jobs import LifecycleRunner
from app.payments.reconciliation import ImmediateReconciliation

settings=get_settings(); engine=make_engine(settings.database_url); sessions=session_factory(engine)
web=FastAPI(title="Rebecca Reseller Bot",docs_url=None,redoc_url=None)
immediate_reconciliation = ImmediateReconciliation()
@web.get("/health")
async def health(): return {"status":"ok","dry_run":settings.dry_run}
web.include_router(payment_router(sessions, settings.plisio_secret_key, settings.plisio_source_currency, immediate_reconciliation))
async def run():
    logging.basicConfig(level=settings.log_level,format="%(asctime)s %(levelname)s %(name)s %(message)s")
    await init_db(engine)
    client = None
    if settings.rebecca_base_url:
        client=HTTPRebeccaClient(settings.rebecca_base_url,settings.rebecca_bearer_token); caps=await client.detect_capabilities()
        async with sessions() as session, session.begin(): session.add(CapabilitySnapshot(capabilities=caps.snapshot()))
    server=Server(Config(web,host=settings.host,port=settings.port,log_level=settings.log_level.lower()))
    tasks=[asyncio.create_task(server.serve())]
    bot=Bot(settings.bot_token) if settings.bot_token else None
    runner = None
    if client:
        runner = LifecycleRunner(sessions, client, NotificationService(bot, settings.owner_ids), settings)
        immediate_reconciliation.runner = runner
        scheduler = AsyncIOScheduler(timezone=settings.timezone)
        scheduler.add_job(runner.run, "interval", seconds=settings.sync_interval_seconds, max_instances=1, coalesce=True)
        scheduler.start()
    if bot:
        dp=Dispatcher(); dp.include_router(owner_router(settings, sessions, runner)); dp.include_router(bot_router(settings, sessions, client, runner)); tasks.append(asyncio.create_task(dp.start_polling(bot)))
    await asyncio.gather(*tasks)
if __name__=="__main__": asyncio.run(run())
