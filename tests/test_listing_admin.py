from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import Request
from pytest import MonkeyPatch
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.db import Base
from app.main_base import auth
from app.models import AuditEvent, CanonicalProperty, Decision, Listing, SourceConfig


def make_listing(
    source: SourceConfig,
    canonical_property: CanonicalProperty,
    *,
    external_id: str,
    decision: Decision,
    archived_at: datetime | None = None,
) -> Listing:
    return Listing(
        source=source,
        canonical_property=canonical_property,
        external_id=external_id,
        url=f"https://example.test/{external_id}",
        title=f"Woning {external_id}",
        address="Teststraat 1",
        city="Groningen",
        decision=decision.value,
        archived_at=archived_at,
    )


def test_archive_review_listings_only_archives_visible_review_rows(
    monkeypatch: MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        source = SourceConfig(name="test", display_name="Test", base_url="https://example.test")
        canonical_property = CanonicalProperty(
            dedup_key="test-property",
            normalized_address="TESTSTRAAT 1",
            city="Groningen",
        )
        previously_archived_at = datetime.now(UTC) - timedelta(days=1)
        visible_review = make_listing(
            source,
            canonical_property,
            external_id="visible-review",
            decision=Decision.REVIEW,
        )
        visible_auto_react = make_listing(
            source,
            canonical_property,
            external_id="visible-auto",
            decision=Decision.AUTO_REACT,
        )
        archived_review = make_listing(
            source,
            canonical_property,
            external_id="archived-review",
            decision=Decision.REVIEW,
            archived_at=previously_archived_at,
        )
        db.add_all([visible_review, visible_auto_react, archived_review])
        db.commit()

        monkeypatch.setattr(auth, "verify_csrf", lambda _request, _token: None)
        request = Request(
            {"type": "http", "method": "POST", "path": "/admin/listings/archive-review", "headers": []}
        )

        response = main_module.archive_review_listings(request, "csrf", db)

        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert visible_review.archived_at is not None
        assert visible_auto_react.archived_at is None
        assert archived_review.archived_at == previously_archived_at.replace(tzinfo=None)
        audit = db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "REVIEW_LISTINGS_ARCHIVED")
        )
        assert audit is not None
        assert audit.data == {"count": 1}
