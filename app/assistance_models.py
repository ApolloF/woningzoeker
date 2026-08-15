from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class AssistanceState(enum.StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    SKIPPED = "SKIPPED"


class AssistanceRequest(Base):
    __tablename__ = "assistance_requests"
    __table_args__ = (
        UniqueConstraint("submission_id"),
        Index("ix_assistance_requests_submission_id", "submission_id"),
        Index("ix_assistance_state_created", "state", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source_configs.id"), index=True)
    state: Mapped[str] = mapped_column(String(30), default=AssistanceState.OPEN.value)
    reason_code: Mapped[str] = mapped_column(String(80))
    summary: Mapped[str] = mapped_column(String(500))
    instructions: Mapped[str] = mapped_column(Text)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
