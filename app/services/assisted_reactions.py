from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import Settings
from app.db import SessionLocal
from app.models import Submission, SubmissionState
from app.schemas import SourceCredentialData
from app.services.assistance import AssistanceService
from app.services.reaction_browser import BrowserReactionResult, ReactionBrowser
from app.services.reactions import DispatchResult, ReactionService


class AssistedReactionService(ReactionService):
    def __init__(
        self,
        settings: Settings | None = None,
        browser: ReactionBrowser | None = None,
    ) -> None:
        super().__init__(settings=settings, browser=browser)
        self.assistance = AssistanceService(self.settings)

    def _finish(
        self,
        submission_id: int,
        listing_id: int,
        source_id: int,
        credential_id: int | None,
        credential: SourceCredentialData | None,
        outcome: BrowserReactionResult,
    ) -> None:
        super()._finish(
            submission_id,
            listing_id,
            source_id,
            credential_id,
            credential,
            outcome,
        )
        if outcome.state in {SubmissionState.REVIEW_REQUIRED, SubmissionState.FAILED}:
            self.assistance.ensure_open(
                submission_id=submission_id,
                listing_id=listing_id,
                source_id=source_id,
                reason_code=outcome.code,
                summary=outcome.summary,
            )
        elif outcome.state == SubmissionState.SENT:
            self.assistance.close_for_submission(submission_id)

    def retry_failed(self) -> list[DispatchResult]:
        stale_before = datetime.now(UTC) - timedelta(
            seconds=self.settings.reaction_browser_timeout_seconds * 2
        )
        with SessionLocal() as db:
            stale = db.scalars(
                select(Submission).where(
                    Submission.state == SubmissionState.PREPARING.value,
                    Submission.last_attempt_at < stale_before,
                )
            ).all()
            for submission in stale:
                submission.state = SubmissionState.FAILED.value
                submission.error_code = "STALE_PREPARATION_RECOVERED"
            failed = db.scalars(
                select(Submission).where(
                    Submission.state == SubmissionState.FAILED.value,
                    Submission.attempt_count < self.settings.reaction_max_attempts,
                )
            ).all()
            retry_ids = [submission.listing_id for submission in failed]
            db.commit()
        return [self.dispatch(listing_id) for listing_id in retry_ids]

    def readiness(self) -> dict[str, object]:
        result = self.assistance.readiness()
        result.pop("contact_probe", None)
        contact_configured = self.contact_is_configured()
        if not contact_configured:
            blockers = list(result["blockers"])
            blockers.append("Privécontactgegevens ontbreken")
            result["blockers"] = blockers
            result["ready"] = False
        result["contact_configured"] = contact_configured
        return result
