from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent


def add_audit(
    db: Session,
    event_type: str,
    summary: str,
    *,
    listing_id: int | None = None,
    source_id: int | None = None,
    data: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_type=event_type,
        summary=summary,
        listing_id=listing_id,
        source_id=source_id,
        data=data or {},
    )
    db.add(event)
    return event
