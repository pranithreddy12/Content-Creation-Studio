from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import ForeignKey, String, DateTime
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class Account(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "accounts"
    clerk_org_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    plan: Mapped[str] = mapped_column(String, default="free", nullable=False)
    stripe_customer: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    region: Mapped[str] = mapped_column(String, default="us-east-1", nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    workspaces: Mapped[list["Workspace"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    members: Mapped[list["AccountMember"]] = relationship(cascade="all, delete-orphan")


class Workspace(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "workspaces"
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String, nullable=False)

    account: Mapped[Account] = relationship(back_populates="workspaces")


class AccountMember(Base):
    __tablename__ = "account_members"
    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
