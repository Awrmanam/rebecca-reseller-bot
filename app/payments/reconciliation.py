from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.audit.redaction import redact

log = logging.getLogger(__name__)


class ImmediateReconciliation:
    """Small indirection shared by HTTP callbacks and the Telegram runtime."""

    runner: Any = None

    def trigger(self) -> asyncio.Task | None:
        if self.runner is None:
            return None
        return schedule_reconciliation(self.runner)


def schedule_reconciliation(runner: Any) -> asyncio.Task:
    """Schedule the shared locked lifecycle path without delaying callbacks."""
    task = asyncio.create_task(runner.reconcile_paid_orders(), name="immediate-order-reconciliation")

    def completed(done: asyncio.Task) -> None:
        if done.cancelled():
            return
        error = done.exception()
        if error is not None:
            log.error("immediate reconciliation failed: %s", redact(str(error)))

    task.add_done_callback(completed)
    return task
