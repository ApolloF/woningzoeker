from __future__ import annotations

from decimal import Decimal
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.services.reactions as reactions_module
from app.config import Settings
from app.db import Base
from app.models import (
    CanonicalProperty,
    Credential,
    Decision,
    Listing,
    PrivateContact,
    SourceConfig,
    SourceMode,
    Submission,
    SubmissionState,
)
from app.schemas import PrivateContactData
from app.services.crypto import CredentialCipher
from app.services.reaction_browser import (
    REACTION_SPECS,
    BrowserReactionResult,
    LoginCheckResult,
    ReactionBrowser,
)
from app.services.reactions import ReactionService


class FakeBrowser:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def react(self, **kwargs: Any) -> BrowserReactionResult:
        self.calls.append(kwargs)
        return BrowserReactionResult(
            state=SubmissionState.SENT,
            code="SUBMIT_COMPLETED",
            summary="sent",
            field_names=["email", "message"],
            browser_result={"submitted": True},
        )

    def check_login(self, source_name: str, credential: Any) -> LoginCheckResult:
        return LoginCheckResult(
            True,
            "LOGIN_OK",
            f"{source_name} login werkt",
            credential.storage_state,
        )


def test_supported_source_specs_cover_all_adapters() -> None:
    assert set(REACTION_SPECS) == {
        "123wonen_groningen",
        "bulten_vastgoed",
        "campus_groningen",
        "funda_rentals",
        "gruno_vastgoed",
        "huurwoningen",
        "maxx_groningen",
        "pandomo",
        "pararius",
        "rotsvast_groningen",
        "woldring",
    }


def test_field_mapping_is_allowlisted() -> None:
    assert ReactionBrowser._field_key("user_email", "email", "INPUT") == "email"
    assert ReactionBrowser._field_key("c_lastname", "text", "INPUT") == "last"
    assert ReactionBrowser._field_key("remark", "text", "TEXTAREA") == "message"
    assert ReactionBrowser._field_key("annual_income", "text", "INPUT") is None


def test_sent_submission_blocks_duplicate_canonical_reaction(monkeypatch: Any) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    testing_session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(reactions_module, "SessionLocal", testing_session)

    key = Fernet.generate_key().decode()
    settings = Settings(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        master_encryption_key=key,
        auto_react_enabled=True,
        dry_run=False,
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
            dedup_key="x" * 64,
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
        duplicate = Listing(
            source_id=source.id,
            canonical_property_id=canonical.id,
            external_id="two",
            url="https://example.test/two",
            title="Dezelfde woning",
            address="Teststraat 1",
            city="Groningen",
            decision=Decision.AUTO_REACT.value,
            response_draft="Nog een reactie.",
            raw_data={"_llm": {"needs_review": False, "error": None}},
        )
        encrypted = CredentialCipher(key).encrypt(contact.model_dump(mode="json"))
        db.add_all([listing, duplicate, PrivateContact(id=1, encrypted_payload=encrypted)])
        db.commit()
        listing_id = listing.id
        duplicate_id = duplicate.id

    fake = FakeBrowser()
    service = ReactionService(settings, browser=fake)  # type: ignore[arg-type]
    first = service.dispatch(listing_id)
    second = service.dispatch(duplicate_id)

    assert first.status == "sent"
    assert second.code == "ALREADY_SENT"
    assert len(fake.calls) == 1
    with testing_session() as db:
        submissions = db.scalars(select(Submission)).all()
        assert len(submissions) == 1
        assert submissions[0].submitted_fields == {"field_names": ["email", "message"]}
        assert "florian@example.test" not in str(submissions[0].submitted_fields)


def test_login_check_persists_verified_status(monkeypatch: Any) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    testing_session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(reactions_module, "SessionLocal", testing_session)
    key = Fernet.generate_key().decode()
    settings = Settings(_env_file=None, master_encryption_key=key)
    with testing_session() as db:
        source = SourceConfig(
            name="woldring",
            display_name="Woldring",
            base_url="https://example.test",
        )
        db.add(source)
        db.flush()
        encrypted = CredentialCipher(key).encrypt(
            {"username": "user@example.test", "password": "secret", "storage_state": None}
        )
        db.add(Credential(source_id=source.id, label="default", encrypted_payload=encrypted))
        db.commit()

    service = ReactionService(settings, browser=FakeBrowser())  # type: ignore[arg-type]
    result = service.verify_credential("woldring")
    assert result["ok"] is True
    with testing_session() as db:
        credential = db.scalar(select(Credential))
        assert credential is not None
        assert credential.last_verified_at is not None
        assert credential.last_error is None
