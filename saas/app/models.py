import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import config
from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # UTC, naive


def token() -> str:
    return secrets.token_urlsafe(24)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    account: Mapped["Account"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")


class Account(Base):
    """Bitta mijoz = bitta OnlinePBX stansiyasi."""
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    pbx_domain: Mapped[str] = mapped_column(String(255), default="")
    pbx_key_enc: Mapped[str] = mapped_column(Text, default="")  # shifrlangan API kalit
    hook_token: Mapped[str] = mapped_column(String(64), unique=True, default=token)
    link_code: Mapped[str] = mapped_column(String(64), unique=True, default=token)  # /start <code>
    plan: Mapped[str] = mapped_column(String(20), default="start")
    trial_ends: Mapped[datetime] = mapped_column(DateTime, default=lambda: now() + timedelta(days=config.TRIAL_DAYS))
    paid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    user: Mapped[User] = relationship(back_populates="account")
    chats: Mapped[list["Chat"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    logs: Mapped[list["CallLog"]] = relationship(back_populates="account", cascade="all, delete-orphan",
                                                 order_by="CallLog.id.desc()")

    @property
    def valid_until(self) -> datetime:
        return max(self.trial_ends, self.paid_until or self.trial_ends)

    @property
    def active(self) -> bool:
        return not self.suspended and self.valid_until > now()

    @property
    def max_chats(self) -> int:
        if self.paid_until and self.paid_until > now():
            return config.PLANS[self.plan]["max_chats"]
        return config.TRIAL_MAX_CHATS


class Chat(Base):
    __tablename__ = "chats"
    __table_args__ = (UniqueConstraint("account_id", "chat_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    chat_id: Mapped[str] = mapped_column(String(32))
    lang: Mapped[str] = mapped_column(String(5), default="uz")
    account: Mapped[Account] = relationship(back_populates="chats")


class CallLog(Base):
    __tablename__ = "call_logs"
    __table_args__ = (UniqueConstraint("account_id", "uuid"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    uuid: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="received")  # received/sent/error/skipped
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    account: Mapped[Account] = relationship(back_populates="logs")


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    plan: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(20), default="manual")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/paid
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    account: Mapped[Account] = relationship()
