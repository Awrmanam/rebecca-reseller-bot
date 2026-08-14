from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.audit.service import audit
from app.config import Settings
from app.database.locks import locked
from app.database.models import Order, OrderStatus, Product, Reseller, ResellerStatus, ResellerUserCache, Setting
from app.notifications.service import NotificationService
from app.rebecca.client import RebeccaClient
from app.reseller.quota import exhausted
from app.reseller.service import generate_password, provision, username_candidate
from app.bot.credentials import credential_message
from app.users.lifecycle import DeletePolicy, deletion_time, safe_delete
from app.users.ownership import verified_owner
from app.payments.service import apply_order
from app.payments.plisio import PlisioClient, normalize_operation
from app.database.models import Payment
from app.database.settings import RuntimeSettingsService

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
        self.runtime = RuntimeSettingsService(settings)

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
        time_thresholds = await self.runtime.time_thresholds(session)
        traffic_thresholds = await self.runtime.traffic_thresholds(session)
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
            reseller.last_known_data_limit = live.data_limit or 0
            reseller.last_known_usage = live.used_traffic
            reseller.last_known_remaining = (live.data_limit or 0) - live.used_traffic
            reseller.expires_at = live.expire
            reseller.last_sync_at = now
            entitlement_key = f"{live.expire}:{live.data_limit}"
            if live.expire:
                days_left = (live.expire - now).total_seconds() / 86400
                for threshold in time_thresholds:
                    if 0 < days_left <= threshold:
                        await self.notifications.once(session, chat_id=reseller.telegram_id, target_type="reseller", target=live.username, kind=f"TIME_{threshold}", entitlement_key=entitlement_key, text=f"⚠️ کمتر از {threshold} روز از اعتبار نمایندگی شما باقی مانده است.")
                        break
            if live.data_limit is not None and live.data_limit > 0:
                percent = max(0, live.data_limit-live.used_traffic) * 100 / live.data_limit
                for threshold in traffic_thresholds:
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
            if reseller.status not in {
                ResellerStatus.ACTIVE,
                ResellerStatus.TRIAL,
                ResellerStatus.LOW_QUOTA,
            }:
                # This entitlement period has already transitioned. Continue
                # live synchronization above, but never repeat mutations or
                # expiration notifications.
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
        grace_hours = await self.runtime.grace_hours(session)
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
                    cached.delete_after = deletion_time(now, grace_hours)
                    cached.local_status = "EXPIRED"
                    if not self.settings.dry_run and self.settings.allow_disable_actions:
                        await self.rebecca.disable_user(live.username)
                        disabled = await self.rebecca.get_user(live.username)
                        if disabled is None or disabled.status.lower() not in {"disabled", "inactive"}:
                            await audit(session, "USER_DISABLE_VERIFY_FAILED", "user", live.username, "ERROR")
                            continue
                        cached.disabled_by_own_expiry = True
                    await audit(session, "USER_DELETE_SCHEDULED", "user", live.username, "OK")
                    await self.notifications.send(reseller.telegram_id, f"⚠️ سرویس {live.username} پایان یافت و تا {grace_hours} ساعت آینده حذف خواهد شد.")
                elif not exhausted(live.expire, live.data_limit, live.used_traffic, now) and cached.delete_after:
                    if cached.disabled_by_own_expiry and reseller.status == ResellerStatus.ACTIVE and not self.settings.dry_run:
                        await self.rebecca.enable_user(live.username)
                        enabled = await self.rebecca.get_user(live.username)
                        if enabled is None or enabled.status.lower() not in {"active", "enabled"}:
                            await audit(session, "USER_RENEW_ENABLE_VERIFY_FAILED", "user", live.username, "ERROR")
                            continue
                    cached.expired_detected_at = None
                    cached.delete_after = None
                    cached.warning_sent_at = None
                    cached.disabled_by_own_expiry = False
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
        async with locked(session, "orders:reconcile", ttl_seconds=300) as won:
            if not won:
                return
            await self._reconcile_orders_locked(session)

    async def reconcile_paid_orders(self) -> None:
        """Run the scheduler's bounded order path from an owner action."""
        async with self.sessions() as session:
            await self._reconcile_orders(session)
            await session.commit()

    async def _reconcile_orders_locked(self, session) -> None:
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
                if await self.runtime.is_dry_run(session):
                    await audit(session, "WOULD_APPLY_ORDER", "order", order.order_number, "DRY_RUN", order_id=order.id)
                    continue
                try:
                    if not reseller.rebecca_admin_username:
                        username = await self._available_username(session, reseller.telegram_username)
                        reseller.rebecca_admin_username = username
                        reseller.status = ResellerStatus.PROVISIONING
                        order.status = OrderStatus.APPLYING
                        order.after_snapshot = {
                            "target": {
                                "username": username,
                                "expire": (datetime.now(UTC) + timedelta(days=product.duration_days)).isoformat(),
                                "data_limit": product.traffic_gb * 1024**3,
                                "services": product.service_ids,
                                "users_limit": product.users_limit,
                            }
                        }
                        # Username and intended entitlement exist durably before
                        # Rebecca sees any create request.
                        await session.commit()
                    if reseller.status == ResellerStatus.PROVISIONING:
                        target = order.after_snapshot["target"]
                        username = reseller.rebecca_admin_username
                        password = generate_password(
                            forbidden=(
                                self.settings.rebecca_bearer_token,
                                self.settings.bot_token,
                                self.settings.plisio_secret_key,
                            )
                        )
                        await provision(
                            self.rebecca,
                            username=username,
                            password=password,
                            expire=datetime.fromisoformat(target["expire"]),
                            data_limit=target["data_limit"],
                            services=target["services"],
                            users_limit=target["users_limit"],
                            telegram_id=reseller.telegram_id,
                        )
                        order.apply_error = "CREDENTIAL_DELIVERY_PENDING"
                        await session.commit()
                        # The password is never persisted or logged. A failed
                        # delivery leaves PROVISIONING and retries by resetting
                        # this same admin's password.
                        # Load immediately before delivery. Runtime settings can
                        # change while the worker is running and require no restart.
                        panel_url = await self.runtime.customer_panel_url(session)
                        text = credential_message(
                            product_name=product.name,
                            traffic_gb=product.traffic_gb,
                            duration_days=product.duration_days,
                            expiry=target["expire"],
                            users_limit=product.users_limit,
                            username=username,
                            password=password,
                            panel_url=panel_url,
                        )
                        sender = getattr(self.notifications, "send_credentials", None)
                        delivered = (
                            await sender(reseller.telegram_id, text, username, password, panel_url)
                            if sender
                            else await self.notifications.send(reseller.telegram_id, text)
                        )
                        if not delivered:
                            raise RuntimeError("credential delivery failed")
                        reseller.status = ResellerStatus.ACTIVE
                        order.status = OrderStatus.APPLIED
                        order.applied_at = datetime.now(UTC)
                        order.apply_error = None
                        order.after_snapshot = (await self.rebecca.get_admin(username)).model_dump(mode="json")
                        await audit(session, "RESELLER_CREATED", "reseller", username, "OK", order_id=order.id)
                        continue
                    changed = await apply_order(order, reseller, product, self.rebecca, datetime.now(UTC), session.commit)
                except Exception as exc:
                    await audit(session, "ORDER_APPLY_FAILED", "order", order.order_number, "ERROR", order_id=order.id, error=str(exc))
                    await self.notifications.owners(f"🚨 اعمال سفارش #{order.order_number} ناموفق بود و پرداخت محفوظ است.")
                else:
                    if changed:
                        await audit(session, "RESELLER_RENEWED", "reseller", reseller.rebecca_admin_username or str(reseller.id), "OK", order_id=order.id, before=order.before_snapshot, after=order.after_snapshot)
                        target = (order.after_snapshot or {}).get("target", {})
                        await self.notifications.send(
                            reseller.telegram_id,
                            f"✅ تمدید سفارش #{order.order_number} انجام شد.\n"
                            f"📦 پلن: {product.name}\n📊 حجم جدید: {product.traffic_gb} GB\n"
                            f"📅 انقضای جدید: {target.get('expire', reseller.expires_at)}\n"
                            f"👥 سقف کاربران: {product.users_limit or 'نامحدود'}",
                        )

    async def _available_username(self, session, telegram_username: str | None) -> str:
        """Reserve candidate selection against local durable reservations."""
        for _ in range(100):
            candidate = username_candidate(telegram_username)
            exists = await session.scalar(
                select(Reseller.id).where(Reseller.rebecca_admin_username == candidate)
            )
            if exists is None and await self.rebecca.get_admin(candidate) is None:
                return candidate
        raise RuntimeError("unable to reserve a unique reseller username")

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
                remote = normalize_operation(await client.transaction(payment.plisio_txn_id))
            except Exception as exc:
                await audit(session, "PLISIO_RECONCILE_FAILED", "order", order.order_number, "ERROR", order_id=order.id, error=str(exc))
                continue
            valid = (
                remote["order_number"] == order.order_number
                and remote["id"] == payment.plisio_txn_id
                and remote["source_currency"] == order.currency.upper()
                and remote["source_amount"] == order.amount
            )
            if not valid:
                await audit(session, "PLISIO_RECONCILE_REJECTED", "order", order.order_number, "REJECTED", order_id=order.id)
                continue
            payment.status = remote["status"]
            if remote["status"] == "completed":
                order.status = OrderStatus.PAID
                order.paid_at = datetime.now(UTC)
                await audit(session, "PLISIO_RECONCILED", "order", order.order_number, "PAID", order_id=order.id)
