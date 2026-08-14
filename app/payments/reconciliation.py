from __future__ import annotations

from typing import Any


class ImmediateReconciliation:
    """Small indirection shared by HTTP callbacks and the Telegram runtime."""

    runner: Any = None

    async def trigger(self) -> bool:
        if self.runner is None:
            return False
        await self.runner.reconcile_paid_orders()
        return True
