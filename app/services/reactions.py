from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import (
    Credential,
    Decision,
    Listing,
    PrivateContact,
    SearchConfig,
    SourceConfig,
    SourceMode,
    Submission,
    SubmissionState,
)
from app.schemas import Criteria, PrivateContactData, SourceCredentialData
from app.services.audit import add_audit
from app.services.crypto import CredentialCipher
from app.services.reaction_browser import BrowserReactionResult, LoginCheckResult, ReactionBrowser

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchResult:
    status: str
    code: str
    submission_id: int | None = None
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "code": self.code,
            "submission_id": self.submission_id,
            "summary": self.summary,
        }


class ReactionService:
    def __init__(
        self,
        settings: Settings | None = None,
        browser: ReactionBrowser | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.browser = browser or ReactionBrowser(self.settings)

    def save_contact(self, contact: PrivateContactData) -> None:
        cipher = self._cipher()
        encrypted = cipher.encrypt(contact.model_dump(mode="json"))
        with SessionLocal() as db:
            record = db.get(PrivateContact, 1)
            if record:
                record.encrypted_payload = encrypted
            else:
                db.add(PrivateContact(id=1, encrypted_payload=encrypted))
            db.commit()

    def save_credential(self, source_name: str, credential: SourceCredentialData) -> None:
        cipher = self._cipher()
        with SessionLocal() as db:
            source = db.scalar(select(SourceConfig).where(SourceConfig.name == source_name))
            if source is None:
                raise ValueError("unknown source")
            record = db.scalar(
                select(Credential).where(
                    Credential.source_id == source.id,
                    Credential.label == "default",
                )
            )
            encrypted = cipher.encrypt(credential.model_dump(mode="json"))
            if record:
                record.encrypted_payload = encrypted
                record.last_error = None
            else:
                db.add(
                    Credential(
                        source_id=source.id,
                        label="default",
                        encrypted_payload=encrypted,
                    )
                )
            db.commit()

    def save_browser_session(self, source_name: str, storage_state: dict[str, Any]) -> None:
        if not isinstance(storage_state, dict) or not storage_state:
            raise ValueError("invalid browser session")
        cipher = self._cipher()
        with SessionLocal() as db:
            source = db.scalar(select(SourceConfig).where(SourceConfig.name == source_name))
            if source is None:
                raise ValueError("unknown source")
            record = db.scalar(
                select(Credential).where(Credential.source_id == source.id, Credential.label == "default")
            )
            existing = (
                SourceCredentialData.model_validate(cipher.decrypt(record.encrypted_payload))
                if record
                else None
            )
            updated = (
                existing.model_copy(update={"storage_state": storage_state})
                if existing
                else SourceCredentialData(storage_state=storage_state)
            )
            encrypted = cipher.encrypt(updated.model_dump(mode="json"))
            if record:
                record.encrypted_payload = encrypted
                record.last_error = None
            else:
                db.add(Credential(source_id=source.id, label="default", encrypted_payload=encrypted))
            db.commit()

    def clear_browser_session(self, source_name: str) -> None:
        cipher = self._cipher()
        with SessionLocal() as db:
            source = db.scalar(select(SourceConfig).where(SourceConfig.name == source_name))
            if source is None:
                raise ValueError("unknown source")
            record = db.scalar(
                select(Credential).where(
                    Credential.source_id == source.id,
                    Credential.label == "default",
                )
            )
            if record is None:
                return
            credential = SourceCredentialData.model_validate(cipher.decrypt(record.encrypted_payload))
            if credential.username and credential.password:
                updated = credential.model_copy(update={"storage_state": None})
                record.encrypted_payload = cipher.encrypt(updated.model_dump(mode="json"))
                record.last_verified_at = None
                record.last_error = None
            else:
                db.delete(record)
            db.commit()

    def credential_statuses(self) -> dict[str, dict[str, Any]]:
        statuses: dict[str, dict[str, Any]] = {}
        with SessionLocal() as db:
            rows = db.execute(
                select(SourceConfig.name, Credential).join(
                    Credential, Credential.source_id == SourceConfig.id
                )
            ).all()
            if not rows:
                return statuses
            try:
                cipher = self._cipher()
            except ValueError:
                return {
                    name: {
                        "configured": False,
                        "has_password": False,
                        "has_browser_session": False,
                        "last_verified_at": record.last_verified_at,
                        "last_error": "Hoofdversleutelingssleutel ontbreekt.",
                    }
                    for name, record in rows
                }
            for name, record in rows:
                try:
                    credential = SourceCredentialData.model_validate(cipher.decrypt(record.encrypted_payload))
                    has_password = bool(credential.username and credential.password)
                    has_browser_session = bool(credential.storage_state)
                except (ValueError, ValidationError):
                    has_password = False
                    has_browser_session = False
                statuses[name] = {
                    "configured": has_password or has_browser_session,
                    "has_password": has_password,
                    "has_browser_session": has_browser_session,
                    "last_verified_at": record.last_verified_at,
                    "last_error": record.last_error,
                }
        return statuses

    def verify_credential(self, source_name: str) -> dict[str, Any]:
        with SessionLocal() as db:
            source = db.scalar(select(SourceConfig).where(SourceConfig.name == source_name))
            if source is None:
                raise ValueError("unknown source")
            record = db.scalar(
                select(Credential).where(
                    Credential.source_id == source.id,
                    Credential.label == "default",
                )
            )
            if record is None:
                return {
                    "ok": False,
                    "code": "CREDENTIALS_MISSING",
                    "summary": "Inloggegevens ontbreken.",
                }
            try:
                credential = SourceCredentialData.model_validate(
                    self._cipher().decrypt(record.encrypted_payload)
                )
            except (ValueError, ValidationError):
                record.last_error = "De opgeslagen inloggegevens konden niet worden gelezen."
                db.commit()
                return {
                    "ok": False,
                    "code": "CREDENTIALS_INVALID",
                    "summary": record.last_error,
                }
            credential_id = record.id
            source_id = source.id
            display_name = source.display_name

        try:
            result = self.browser.check_login(source_name, credential)
        except Exception as exc:
            logger.exception(
                "credential check failed",
                extra={"context": {"source": source_name}},
            )
            result = LoginCheckResult(
                False,
                "LOGIN_CHECK_ERROR",
                f"Inlogcontrole mislukt: {type(exc).__name__}",
            )
        with SessionLocal() as db:
            record = db.get(Credential, credential_id)
            if record is not None:
                if result.storage_state:
                    updated = credential.model_copy(update={"storage_state": result.storage_state})
                    record.encrypted_payload = self._cipher().encrypt(updated.model_dump(mode="json"))
                record.last_verified_at = datetime.now(UTC) if result.ok else None
                record.last_error = None if result.ok else result.summary[:500]
            add_audit(
                db,
                "CREDENTIAL_CHECKED",
                f"{display_name}: {'inloggen werkt' if result.ok else result.summary}",
                source_id=source_id,
                data={"ok": result.ok, "code": result.code},
            )
            db.commit()
        return {
            "ok": result.ok,
            "code": result.code,
            "summary": result.summary,
        }

    def contact_is_configured(self) -> bool:
        with SessionLocal() as db:
            return db.get(PrivateContact, 1) is not None

    def dispatch(self, listing_id: int, *, force: bool = False) -> DispatchResult:
        with SessionLocal() as db:
            listing = db.get(Listing, listing_id)
            if listing is None:
                return DispatchResult("skipped", "LISTING_NOT_FOUND")
            source = db.get(SourceConfig, listing.source_id)
            if source is None:
                return DispatchResult("skipped", "SOURCE_NOT_FOUND")
            if not force and not self.settings.auto_react_enabled:
                return DispatchResult("skipped", "AUTO_REACT_DISABLED")
            if not force and source.mode != SourceMode.AUTO_REACT.value:
                return DispatchResult("skipped", "SOURCE_NOT_AUTO_REACT")
            if not force and listing.decision != Decision.AUTO_REACT.value:
                return DispatchResult("skipped", "LISTING_NOT_AUTO_REACT")
            if not listing.response_draft:
                return DispatchResult("skipped", "MESSAGE_MISSING")
            search_config = db.get(SearchConfig, 1)
            criteria = Criteria.model_validate(search_config.config) if search_config else Criteria()

            existing = db.scalar(
                select(Submission).where(
                    Submission.canonical_property_id == listing.canonical_property_id
                )
            )
            if existing:
                if existing.state == SubmissionState.SENT.value:
                    return DispatchResult(
                        "skipped", "ALREADY_SENT", existing.id, "Voor deze woning is al gereageerd."
                    )
                if existing.state == SubmissionState.PREPARING.value:
                    return DispatchResult("skipped", "IN_PROGRESS", existing.id)
                if existing.attempt_count >= self.settings.reaction_max_attempts and not force:
                    return DispatchResult("skipped", "MAX_ATTEMPTS", existing.id)
                submission = existing
                submission.listing_id = listing.id
            else:
                submission = Submission(
                    listing_id=listing.id,
                    canonical_property_id=listing.canonical_property_id,
                    state=SubmissionState.INTENDED.value,
                    exact_text=listing.response_draft,
                )
                db.add(submission)
                try:
                    db.flush()
                except IntegrityError:
                    db.rollback()
                    concurrent = db.scalar(
                        select(Submission).where(
                            Submission.canonical_property_id == listing.canonical_property_id
                        )
                    )
                    return DispatchResult(
                        "skipped",
                        "CONCURRENT_CLAIM",
                        concurrent.id if concurrent else None,
                    )

            submission.state = SubmissionState.PREPARING.value
            submission.attempt_count += 1
            submission.last_attempt_at = datetime.now(UTC)
            submission.error_code = None
            submission.exact_text = listing.response_draft
            add_audit(
                db,
                "REACTION_PREPARING",
                f"Reactiepoging voorbereid voor {source.display_name}",
                listing_id=listing.id,
                source_id=source.id,
                data={"attempt": submission.attempt_count, "force": force},
            )
            db.commit()
            submission_id = submission.id
            listing_url = listing.url
            message = listing.response_draft
            source_name = source.name
            source_id = source.id
            accept_legal_confirmations = criteria.auto_accept_legal_confirmations

        try:
            contact = self._load_contact()
            credential, credential_id = self._load_credential(source_id)
        except (ValueError, ValidationError) as exc:
            code = "PRIVATE_DATA_INVALID" if isinstance(exc, ValidationError) else "PRIVATE_DATA_MISSING"
            return self._finish_without_browser(
                submission_id,
                listing_id,
                source_id,
                code,
                "Contact- of inloggegevens ontbreken of zijn ongeldig.",
            )

        allow_submit = self.settings.auto_react_enabled and not self.settings.dry_run
        try:
            outcome = self.browser.react(
                source_name=source_name,
                listing_url=listing_url,
                message=message,
                contact=contact,
                credential=credential,
                submission_id=submission_id,
                allow_submit=allow_submit,
                accept_legal_confirmations=accept_legal_confirmations,
            )
        except Exception as exc:
            logger.exception(
                "reaction execution failed",
                extra={"context": {"listing_id": listing_id, "source": source_name}},
            )
            return self._finish_without_browser(
                submission_id,
                listing_id,
                source_id,
                "BROWSER_ERROR",
                f"Browserautomatisering mislukte: {type(exc).__name__}",
                state=SubmissionState.FAILED,
            )

        self._finish(submission_id, listing_id, source_id, credential_id, credential, outcome)
        return DispatchResult(
            status=outcome.state.value.lower(),
            code=outcome.code,
            submission_id=submission_id,
            summary=outcome.summary,
        )

    def _load_contact(self) -> PrivateContactData:
        cipher = self._cipher()
        with SessionLocal() as db:
            record = db.get(PrivateContact, 1)
            if record is None:
                raise ValueError("private contact not configured")
            return PrivateContactData.model_validate(cipher.decrypt(record.encrypted_payload))

    def _load_credential(self, source_id: int) -> tuple[SourceCredentialData | None, int | None]:
        cipher = self._cipher()
        with SessionLocal() as db:
            record = db.scalar(
                select(Credential).where(
                    Credential.source_id == source_id,
                    Credential.label == "default",
                )
            )
            if record is None:
                return None, None
            payload = SourceCredentialData.model_validate(cipher.decrypt(record.encrypted_payload))
            return payload, record.id

    def _finish_without_browser(
        self,
        submission_id: int,
        listing_id: int,
        source_id: int,
        code: str,
        summary: str,
        *,
        state: SubmissionState = SubmissionState.REVIEW_REQUIRED,
    ) -> DispatchResult:
        outcome = BrowserReactionResult(state=state, code=code, summary=summary)
        self._finish(submission_id, listing_id, source_id, None, None, outcome)
        return DispatchResult(state.value.lower(), code, submission_id, summary)

    def _finish(
        self,
        submission_id: int,
        listing_id: int,
        source_id: int,
        credential_id: int | None,
        credential: SourceCredentialData | None,
        outcome: BrowserReactionResult,
    ) -> None:
        with SessionLocal() as db:
            submission = db.get(Submission, submission_id)
            listing = db.get(Listing, listing_id)
            if submission is None or listing is None:
                return
            submission.state = outcome.state.value
            submission.error_code = outcome.code
            submission.submitted_fields = {"field_names": outcome.field_names}
            submission.browser_result = outcome.browser_result
            submission.before_screenshot = outcome.before_screenshot
            submission.after_screenshot = outcome.after_screenshot
            if outcome.state == SubmissionState.SENT:
                submission.completed_at = datetime.now(UTC)
                listing.response_sent = True

            if credential_id and credential:
                record = db.get(Credential, credential_id)
                if record:
                    if outcome.storage_state:
                        updated = credential.model_copy(update={"storage_state": outcome.storage_state})
                        record.encrypted_payload = self._cipher().encrypt(updated.model_dump(mode="json"))
                    if outcome.code in {
                        "LOGIN_FAILED",
                        "LOGIN_FORM_UNKNOWN",
                        "CAPTCHA_REQUIRED",
                        "REAUTHENTICATION_REQUIRED",
                    }:
                        record.last_error = outcome.summary[:500]
                    else:
                        record.last_error = None
                        record.last_verified_at = datetime.now(UTC)

            add_audit(
                db,
                f"REACTION_{outcome.state.value}",
                outcome.summary[:500],
                listing_id=listing_id,
                source_id=source_id,
                data={
                    "submission_id": submission_id,
                    "code": outcome.code,
                    "field_names": outcome.field_names,
                },
            )
            db.commit()

    def _cipher(self) -> CredentialCipher:
        return CredentialCipher(self.settings.master_encryption_key)
