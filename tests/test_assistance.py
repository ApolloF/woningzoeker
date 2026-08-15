from __future__ import annotations

from decimal import Decimal
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.services.assistance as assistance_module
import app.services.assisted_reactions as assisted_module
import app.services.reactions as reactions_module
from app.assistance_models import AssistanceRequest, AssistanceState
from app.config import Settings
from app.db import Base
from app.models import (
    CanonicalProperty,
    Decision,
    Listing,
    PrivateContact,
    SourceConfig,
    SourceMode,
    Submission,
    SubmissionState,
)
from app.schemas import PrivateContactData
from app.services.assisted_reactions import AssistedReactionService
from app.services.crypto import CredentialCipher
from app.services.reaction_browser import BrowserReactionResult


class ReviewBrowser:
    def react(self, **_: Any) -> BrowserReactionResult:
        return BrowserReactionResult(
            state=SubmissionState.REVIEW_REQUIRED,
            code="CAPTCHA_REQUIRED",
            summary="Menselijke controle nodig.",
        )


def test_review_creates_assistance_and_user_can_confirm(monkeypatch: Any) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    testing_session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(reactions_module, "SessionLocal", testing_session)
    monkeypatch.setattr(assistance_module, "SessionLocal", testing_session)
    monkeypatch.setattr(assisted_module, "SessionLocal", testing_session)

    key = Fernet.generate_key().decode()
    settings = Settings(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        master_encryption_key=key,
        auto_react_enabled=True,
        dry_run=False,
        scheduler_enabled=True,
        llm_provider="openai",
        openai_api_key="test",
    )
    contact = PrivateContactData(
        first_name="Florian",
        last_name="Tester",
        initials="F.",
        email="florian@example.test",
        phone="0612345678",
        address="Teststraat",
        house_number="1",
        city="Groningen",
    )
    with testing_session() as db:
        source = SourceConfig(
            name="funda_rentals",
            display_name="Funda",
            base_url="https://example.test",
            mode=SourceMode.AUTO_REACT.value,
        )
        canonical = CanonicalProperty(
            dedup_key="a" * 64,
            normalized_address="teststraat 1",
            city="Groningen",
            rent_total=Decimal("1200"),
        )
        db.add_all([source, canonical])
        db.flush()
        listing = Listing(
            source_id=source.id,
            canonical_property_id=canonical.id,
            external_id="one",
            url="https://example.test/one",
            title="Testwoning",
            address="Teststraat 1",
            city="Groningen",
            decision=Decision.AUTO_REACT.value,
            response_draft="Ik heb interesse.",
            raw_data={"_llm": {"needs_review": False, "error": None}},
        )
        encrypted = CredentialCipher(key).encrypt(contact.model_dump(mode="json"))
        db.add_all([listing, PrivateContact(id=1, encrypted_payload=encrypted)])
        db.commit()
        listing_id = listing.id

    service = AssistedReactionService(settings, browser=ReviewBrowser())  # type: ignore[arg-type]
    result = service.dispatch(listing_id)
    assert result.code == "CAPTCHA_REQUIRED"

    with testing_session() as db:
        assistance = db.scalar(select(AssistanceRequest))
        assert assistance is not None
        assert assistance.state == AssistanceState.OPEN.value
        assistance_id = assistance.id

    confirmed = service.assistance.confirm_manual_submission(assistance_id, "Handmatig gedaan")
    assert confirmed.ok
    with testing_session() as db:
        assistance = db.get(AssistanceRequest, assistance_id)
        submission = db.scalar(select(Submission))
        listing = db.get(Listing, listing_id)
        assert assistance is not None and assistance.state == AssistanceState.RESOLVED.value
        assert submission is not None and submission.state == SubmissionState.SENT.value
        assert listing is not None and listing.response_sent is True
