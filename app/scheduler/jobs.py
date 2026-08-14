import asyncio, logging
log=logging.getLogger(__name__)
class LifecycleRunner:
    def __init__(self): self._lock=asyncio.Lock()
    async def run(self):
        if self._lock.locked(): return
        async with self._lock: log.info("lifecycle_batch",extra={"batch_size":50})
