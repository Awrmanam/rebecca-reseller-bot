from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.audit.service import audit
from app.config import Settings
from app.database.locks import locked
from app.database.models import Order, OrderStatus, Product, Reseller, ResellerStatus, ResellerUserCache
from app.notifications.service import NotificationService
from app.rebecca.client import RebeccaClient
from app.reseller.quota import exhausted
from app.reseller.service import credentials, provision
from app.users.lifecycle import DeletePolicy, deletion_time, safe_delete
from app.users.ownership import verified_owner
from app.payments.service import apply_order
from app.payments.plisio import PlisioClient, exact_amount
from app.database.models import Payment

log = logging.getLogger(__name__)


class LifecycleRunner:
    """Bounded, restart-safe lifecycle coordinator.

    Rebecca remains authoritative. Every mutation is preceded and followed by a
    live read in the domain operation that performs it.
    """

    def __init__(
        self,
        sessions: async_sessionmaker,
        rebecca: RebeccaClient,
        notifications: NotificationService,
        settings: Settings,
        *,
        batch_size: int = 50,
    ):
        self.sessions = sessions
        self.rebecca = rebecca
        self.notifications = notifications
        self.settings = settings
        self.batch_size = batch_size

    async def run(self) -> None:
        async with self.sessions() as session:
            async with locked(session, "scheduler:global", ttl_seconds=240) as won:
                if not won:
                    return
                await self._sync_resellers(session)
                await self._sync_users(session)
                await self._due_deletions(session)
                await self._reconcile_orders(session)
                await self._reconcile_plisio(session)
                await session.commit()

    async def _sync_resellers(self, session) -> None:
        rows = (
            await session.scalars(
                select(Reseller)
                .where(Reseller.status != ResellerStatus.SUSPENDED)
                .limit(self.batch_size)
            )
        ).all()
        now = datetime.now(UTC)
        for reseller in rows:
            if reseller.automation_hold or not reseller.rebecca_admin_username:
                continue
            try:
                live = await self.rebecca.get_admin(reseller.rebecca_admin_username)
            except Exception as exc:
                await audit(session, "REBECCA_UNAVAILABLE", "reseller", str(reseller.id), "ERROR", error=str(exc))
                continue
            if live is None:
                continue
            reseller.last_known_data_limit = live.data_limit
            reseller.last_known_usage = live.used_traffic
            reseller.last_known_remaining = live.data_limit - live.used_traffic
            reseller.expires_at = live.expire
            reseller.last_sync_at = now
            entitlement_key = f"{live.expire}:{live.data_limit}"
            if live.expire:
                days_left = (live.expire - now).total_seconds() / 86400
                for threshold in self.settings.time_warning_thresholds:
                    if 0 < days_left <= threshold:
                        await self.notifications.once(session, chat_id=reseller.telegram_id, target_type="reseller", target=live.username, kind=f"TIME_{threshold}", entitlement_key=entitlement_key, text=f"⚠️ کمتر از {threshold} روز از اعتبار نمایندگی شما باقی مانده است.")
                        break
            if live.data_limit > 0:
                percent = max(0, live.data_limit-live.used_traffic) * 100 / live.data_limit
                for threshold in self.settings.traffic_warning_thresholds:
                    if 0 < percent <= threshold:
                        await self.notifications.once(session, chat_id=reseller.telegram_id, target_type="reseller", target=live.username, kind=f"TRAFFIC_{threshold}", entitlement_key=entitlement_key, text=f"⚠️ کمتر از {threshold}٪ حجم نمایندگی شما باقی مانده است.")
                        break
            if not exhausted(live.expire, live.data_limit, live.used_traffic, now):
                if reseller.status == ResellerStatus.ACTIVE and not self.settings.dry_run:
                    parent_disabled = (await session.scalars(select(ResellerUserCache).where(ResellerUserCache.reseller_id == reseller.id, ResellerUserCache.disabled_by_parent_reseller))).all()
                    for cached in parent_disabled:
                        child = await self.rebecca.get_user(cached.username)
                        if child and verified_owner(child, live.username) and not exhausted(child.expire, child.data_limit, child.used_traffic, now):
                            await self.rebecca.enable_user(child.username)
                            cached.disabled_by_parent_reseller = False
                continue
            if self.settings.dry_run or not self.settings.allow_disable_actions:
                await audit(session, "WOULD_DISABLE_RESELLER", "reseller", live.username, "DRY_RUN")
                continue
            await self.rebecca.disable_admin(live.username)
            if getattr((await self.rebecca.detect_capabilities()), "admin_users_disable"):
                await self.rebecca.disable_admin_users(live.username)
                await session.execute(update(ResellerUserCache).where(ResellerUserCache.reseller_id == reseller.id, ResellerUserCache.local_status == "ACTIVE").values(disabled_by_parent_reseller=True))
            verified = await self.rebecca.get_admin(live.username)
            if verified is None or verified.status.lower() not in {"disabled", "inactive"}:
                await audit(session, "RESELLER_DISABLE_VERIFY_FAILED", "reseller", live.username, "ERROR")
                continue
            reseller.status = ResellerStatus.EXPIRED
            await audit(session, "RESELLER_DISABLED", "reseller", live.username, "OK", before=live.model_dump(mode="json"), after=verified.model_dump(mode="json"))
            await self.notifications.send(reseller.telegram_id, "⛔ اعتبار نمایندگی شما به پایان رسید. اطلاعات شما برای تمدید محفوظ است.")

    async def _sync_users(self, session) -> None:
        resellers = (
            await session.scalars(select(Reseller).limit(self.batch_size))
        ).all()
        now = datetime.now(UTC)
        for reseller in resellers:
            if reseller.automation_hold or not reseller.rebecca_admin_username:
                continue
            try:
                users = await self.rebecca.list_admin_users(reseller.rebecca_admin_username)
            except Exception:
                continue
            for live in users[: self.batch_size]:
                if not verified_owner(live, reseller.rebecca_admin_username):
                    continue
                cached = await session.scalar(
                    select(ResellerUserCache).where(
                        ResellerUserCache.reseller_id == reseller.id,
                        ResellerUserCache.username == live.username,
                    )
                )
                if cached is None:
                    cached = ResellerUserCache(
                        reseller_id=reseller.id,
                        username=live.username,
                        rebecca_admin_username=reseller.rebecca_admin_username,
                        status=live.status,
                    )
                    session.add(cached)
                cached.status = live.status
                cached.expire = live.expire
                cached.data_limit = live.data_limit
                cached.used_traffic = live.used_traffic
                cached.last_seen_at = now
                if exhausted(live.expire, live.data_limit, live.used_traffic, now) and cached.expired_detected_at is None:
                    cached.expired_detected_at = now
                    cached.delete_after = deletion_time(now, self.settings.user_delete_grace_hours)
                    cached.local_status = "EXPIRED"
                    if not self.settings.dry_run and self.settings.allow_disable_actions:
                        await self.rebecca.disable_user(live.username)
                    await audit(session, "USER_DELETE_SCHEDULED", "user", live.username, "OK")
                    await self.notifications.send(reseller.telegram_id, f"⚠️ سرویس {live.username} پایان یافت و تا {self.settings.user_delete_grace_hours} ساعت آینده حذف خواهد شد.")
                elif not exhausted(live.expire, live.data_limit, live.used_traffic, now) and cached.delete_after:
                    cached.expired_detected_at = None
                    cached.delete_after = None
                    cached.local_status = "ACTIVE"
                    await audit(session, "USER_DELETE_CANCELLED", "user", live.username, "RENEWED")

    async def _due_deletions(self, session) -> None:
        now = datetime.now(UTC)
        rows = (
            await session.scalars(
                select(ResellerUserCache)
                .where(ResellerUserCache.delete_after <= now + timedelta(hours=24))
                .limit(self.batch_size)
            )
        ).all()
        for cached in rows:
            reseller = await session.get(Reseller, cached.reseller_id)
            if reseller is None:
                continue
            hours_left = (cached.delete_after - now).total_seconds() / 3600
            if 0 < hours_left <= 24 and cached.warning_sent_at is None:
                if await self.notifications.send(reseller.telegram_id, f"⚠️ کاربر {cached.username} در کمتر از ۲۴ ساعت حذف خواهد شد؛ در صورت تمدید حذف خودکار لغو می‌شود."):
                    cached.warning_sent_at = now
            result = await safe_delete(
                self.rebecca,
                cached.username,
                cached.rebecca_admin_username,
                cached.delete_after,
                now,
                DeletePolicy(
                    self.settings.dry_run,
                    self.settings.destructive_actions,
                    self.settings.allow_delete_actions,
                ),
                hold=cached.deletion_hold or reseller.automation_hold,
            )
            await audit(session, result, "user", cached.username, result)
            if result == "DELETED":
                cached.local_status = "DELETED"
                cached.delete_after = None
                await self.notifications.send(reseller.telegram_id, f"🗑 کاربر {cached.username} پس از پایان مهلت حذف شد.")

    async def _reconcile_orders(self, session) -> None:
        # PAID/APPLYING orders remain durable and visible for the payment worker.
        # Claiming and Rebecca entitlement application are intentionally separate
        # from the lifecycle transaction to keep retries bounded.
        orders = (
            await session.scalars(
                select(Order)
                .where(Order.status.in_([OrderStatus.PAID, OrderStatus.APPLYING]))
                .limit(self.batch_size)
            )
        ).all()
        for order in orders:
            async with locked(session, f"order:{order.id}", ttl_seconds=300) as won:
                if not won:
                    continue
                reseller = await session.get(Reseller, order.reseller_id)
                product = await session.get(Product, order.product_id)
                if reseller is None or product is None:
                    await audit(session, "ORDER_APPLY_FAILED", "order", order.order_number, "ERROR", order_id=order.id, error="missing reseller/product")
                    continue
                if self.settings.dry_run:
                    await audit(session, "WOULD_APPLY_ORDER", "order", order.order_number, "DRY_RUN", order_id=order.id)
                    continue
                try:
                    if not reseller.rebecca_admin_username:
                        username, password = credentials()
                        await provision(
                            self.rebecca,
                            username=username,
                            password=password,
                            expire=datetime.now(UTC) + timedelta(days=product.duration_days),
                            data_limit=product.traffic_gb * 1024**3,
                            services=product.service_ids,
                            telegram_id=reseller.telegram_id,
                        )
                        reseller.rebecca_admin_username = username
                        reseller.status = ResellerStatus.ACTIVE
                        # The password is never persisted or logged.
                        await self.notifications.send(reseller.telegram_id, f"✅ نمایندگی شما ساخته شد.\nنام کاربری: `{username}`\nرمز عبور: `{password}`\nرمز فقط همین بار نمایش داده می‌شود.")
                        order.status = OrderStatus.APPLIED
                        order.applied_at = datetime.now(UTC)
                        await audit(session, "RESELLER_CREATED", "reseller", username, "OK", order_id=order.id)
                        continue
                    changed = await apply_order(order, reseller, product, self.rebecca, datetime.now(UTC), session.commit)
                except Exception as exc:
                    await audit(session, "ORDER_APPLY_FAILED", "order", order.order_number, "ERROR", order_id=order.id, error=str(exc))
                    await self.notifications.owners(f"🚨 اعمال سفارش #{order.order_number} ناموفق بود و پرداخت محفوظ است.")
                else:
                    if changed:
                        await audit(session, "RESELLER_RENEWED", "reseller", reseller.rebecca_admin_username or str(reseller.id), "OK", order_id=order.id, before=order.before_snapshot, after=order.after_snapshot)
                        await self.notifications.send(reseller.telegram_id, f"✅ سفارش #{order.order_number} با موفقیت اعمال شد.")

    async def _reconcile_plisio(self, session) -> None:
        if not self.settings.plisio_enabled or not self.settings.plisio_secret_key:
            return
        rows = (
            await session.execute(
                select(Order, Payment)
                .join(Payment, Payment.order_id == Order.id)
                .where(
                    Order.status == OrderStatus.WAITING_PAYMENT,
                    Payment.method == "PLISIO",
                )
                .limit(self.batch_size)
            )
        ).all()
        client = PlisioClient(self.settings.plisio_secret_key)
        for order, payment in rows:
            if not payment.plisio_txn_id:
                continue
            try:
                remote = await client.transaction(payment.plisio_txn_id)
            except Exception as exc:
                await audit(session, "PLISIO_RECONCILE_FAILED", "order", order.order_number, "ERROR", order_id=order.id, error=str(exc))
                continue
            valid = (
                str(remote.get("order_number")) == order.order_number
                and str(remote.get("txn_id")) == payment.plisio_txn_id
                and str(remote.get("source_currency", "")).upper() == order.currency.upper()
                and exact_amount(remote.get("source_amount", "-1")) == order.amount
            )
            if not valid:
                await audit(session, "PLISIO_RECONCILE_REJECTED", "order", order.order_number, "REJECTED", order_id=order.id)
                continue
            payment.status = str(remote.get("status", "unknown"))
            if remote.get("status") == "completed":
                order.status = OrderStatus.PAID
                order.paid_at = datetime.now(UTC)
                await audit(session, "PLISIO_RECONCILED", "order", order.order_number, "PAID", order_id=order.id)
