from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def now() -> datetime: return datetime.now(timezone.utc)
class Base(DeclarativeBase): pass
class ResellerStatus(StrEnum):
    PROVISIONING="PROVISIONING"; TRIAL="TRIAL"; ACTIVE="ACTIVE"; LOW_QUOTA="LOW_QUOTA"; EXPIRED="EXPIRED"; SUSPENDED="SUSPENDED"; DISABLED="DISABLED"; ERROR="ERROR"
class OrderStatus(StrEnum):
    PENDING="PENDING"; WAITING_RECEIPT="WAITING_RECEIPT"; WAITING_PAYMENT="WAITING_PAYMENT"; PAID="PAID"; APPLYING="APPLYING"; APPLIED="APPLIED"; REJECTED="REJECTED"; EXPIRED="EXPIRED"; CANCELLED="CANCELLED"; FAILED="FAILED"
class Reseller(Base):
    __tablename__="resellers"
    id: Mapped[int]=mapped_column(primary_key=True); telegram_id: Mapped[int]=mapped_column(BigInteger, unique=True); telegram_username: Mapped[str|None]=mapped_column(String(64)); rebecca_admin_username: Mapped[str|None]=mapped_column(String(64), unique=True)
    status: Mapped[str]=mapped_column(String(24), default=ResellerStatus.PROVISIONING); product_id: Mapped[int|None]=mapped_column(ForeignKey("products.id")); created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now); activated_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); expires_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); purchased_traffic_bytes: Mapped[int]=mapped_column(BigInteger, default=0); last_known_data_limit: Mapped[int]=mapped_column(BigInteger, default=0); last_known_usage: Mapped[int]=mapped_column(BigInteger, default=0); last_known_remaining: Mapped[int]=mapped_column(BigInteger, default=0); trial: Mapped[bool]=mapped_column(default=False); trial_used: Mapped[bool]=mapped_column(default=False); suspended_reason: Mapped[str|None]=mapped_column(Text); last_sync_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); automation_hold: Mapped[bool]=mapped_column(default=False)
class Product(Base):
    __tablename__="products"
    id: Mapped[int]=mapped_column(primary_key=True); name: Mapped[str]=mapped_column(String(100)); slug: Mapped[str]=mapped_column(String(64), unique=True); enabled: Mapped[bool]=mapped_column(default=True); deleted: Mapped[bool]=mapped_column(default=False); service_type: Mapped[str]=mapped_column(String(16)); service_ids: Mapped[list[Any]]=mapped_column(JSON, default=list); duration_days: Mapped[int]; traffic_gb: Mapped[int]; price_toman: Mapped[Decimal]=mapped_column(Numeric(18,0)); users_limit: Mapped[int|None]; created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now); updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now, onupdate=now)
class Order(Base):
    __tablename__="orders"
    id: Mapped[int]=mapped_column(primary_key=True); order_number: Mapped[str]=mapped_column(String(32), unique=True); reseller_id: Mapped[int]=mapped_column(ForeignKey("resellers.id")); product_id: Mapped[int]=mapped_column(ForeignKey("products.id")); amount: Mapped[Decimal]=mapped_column(Numeric(18,2)); currency: Mapped[str]=mapped_column(String(8)); status: Mapped[str]=mapped_column(String(24), default=OrderStatus.PENDING); payment_method: Mapped[str]=mapped_column(String(16)); created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now); expires_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); paid_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); applied_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); before_snapshot: Mapped[dict|None]=mapped_column(JSON); after_snapshot: Mapped[dict|None]=mapped_column(JSON); apply_error: Mapped[str|None]=mapped_column(Text)
class Payment(Base):
    __tablename__="payments"
    id: Mapped[int]=mapped_column(primary_key=True); order_id: Mapped[int]=mapped_column(ForeignKey("orders.id"), unique=True); method: Mapped[str]=mapped_column(String(16)); status: Mapped[str]=mapped_column(String(24)); telegram_file_id: Mapped[str|None]=mapped_column(String(255)); metadata_json: Mapped[dict]=mapped_column(JSON, default=dict); plisio_txn_id: Mapped[str|None]=mapped_column(String(128), unique=True); invoice_url: Mapped[str|None]=mapped_column(Text)
class TrialRecord(Base):
    __tablename__="trial_records"
    id: Mapped[int]=mapped_column(primary_key=True); telegram_id: Mapped[int]=mapped_column(BigInteger, unique=True); used_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now); admin_username: Mapped[str|None]=mapped_column(String(64)); expires_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); status: Mapped[str]=mapped_column(String(24), default="RESERVED")
class ResellerUserCache(Base):
    __tablename__="reseller_users"; __table_args__=(UniqueConstraint("rebecca_admin_username","username"),)
    id: Mapped[int]=mapped_column(primary_key=True); reseller_id: Mapped[int]=mapped_column(ForeignKey("resellers.id")); username: Mapped[str]=mapped_column(String(64)); rebecca_admin_username: Mapped[str]=mapped_column(String(64)); status: Mapped[str]=mapped_column(String(24)); expire: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); data_limit: Mapped[int]=mapped_column(BigInteger, default=0); used_traffic: Mapped[int]=mapped_column(BigInteger, default=0); first_seen_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now); last_seen_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now); local_status: Mapped[str]=mapped_column(String(24), default="ACTIVE"); expired_detected_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); delete_after: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); warning_sent_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); deletion_hold: Mapped[bool]=mapped_column(default=False); disabled_by_parent_reseller: Mapped[bool]=mapped_column(default=False); disabled_by_own_expiry: Mapped[bool]=mapped_column(default=False)
class RequiredChannel(Base):
    __tablename__="required_channels"; id: Mapped[int]=mapped_column(primary_key=True); chat_id: Mapped[str]=mapped_column(String(128), unique=True); join_url: Mapped[str]; title: Mapped[str]; enabled: Mapped[bool]=mapped_column(default=True)
class WarningEvent(Base):
    __tablename__="warning_events"; __table_args__=(UniqueConstraint("target_type","target_identifier","kind","entitlement_key"),)
    id: Mapped[int]=mapped_column(primary_key=True); target_type: Mapped[str]; target_identifier: Mapped[str]; kind: Mapped[str]; entitlement_key: Mapped[str]; created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
class AuditLog(Base):
    __tablename__="audit_logs"; id: Mapped[int]=mapped_column(primary_key=True); timestamp: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now); actor: Mapped[str]; actor_type: Mapped[str]; action: Mapped[str]; target_type: Mapped[str]; target_identifier: Mapped[str]; order_id: Mapped[int|None]=mapped_column(ForeignKey("orders.id")); before_snapshot: Mapped[dict|None]=mapped_column(JSON); after_snapshot: Mapped[dict|None]=mapped_column(JSON); result: Mapped[str]; error: Mapped[str|None]=mapped_column(Text)
class LifecycleLock(Base):
    __tablename__="lifecycle_locks"; key: Mapped[str]=mapped_column(String(150), primary_key=True); acquired_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now); expires_at: Mapped[datetime]
class Setting(Base):
    __tablename__="settings"; key: Mapped[str]=mapped_column(String(100), primary_key=True); value: Mapped[Any]=mapped_column(JSON)
class CapabilitySnapshot(Base):
    __tablename__="capability_snapshots"; id: Mapped[int]=mapped_column(primary_key=True); detected_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now); capabilities: Mapped[dict]=mapped_column(JSON)
