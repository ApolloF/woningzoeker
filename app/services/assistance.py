from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from app.assistance_models import AssistanceRequest, AssistanceState
from app.config import Settings
from app.db import SessionLocal
from app.models import Decision, Listing, SearchConfig, SourceConfig, Submission, SubmissionState
from app.schemas import Criteria
from app.services.audit import add_audit

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssistanceOutcome:
    ok: bool
    code: str


INSTRUCTION_MAP = {
    "CAPTCHA_REQUIRED": (
        "Open de advertentie, log zo nodig in en voltooi de CAPTCHA zelf. Controleer daarna het "
        "conceptbericht, verstuur de reactie op de site en bevestig hier dat dit gelukt is."
    ),
    "LEGAL_CONFIRMATION_REQUIRED": (
        "Open de advertentie en lees de voorwaarden of privacyverklaring persoonlijk. Vink deze "
        "alleen aan als je ermee instemt, verstuur de reactie en bevestig het resultaat hier."
    ),
    "CREDENTIALS_MISSING": (
        "Sla eerst de accountgegevens voor deze bron versleuteld op bij Instellingen en kies daarna "
        "Opnieuw proberen. Je kunt de reactie ook handmatig op de bronsite versturen."
    ),
    "LOGIN_REQUIRED": (
        "De opgeslagen sessie is verlopen. Controleer de accountgegevens bij Instellingen en kies "
        "Opnieuw proberen, of reageer handmatig via de bronsite."
    ),
    "REAUTHENTICATION_REQUIRED": (
        "De site vraagt opnieuw om Google-inloggen, twee-factorverificatie of een nieuwe sessie. "
        "Werk de beveiligde browsersessie bij onder Instellingen en kies daarna Opnieuw proberen."
    ),
    "LOGIN_FAILED": (
        "Controleer gebruikersnaam en wachtwoord bij Instellingen. Als de site extra verificatie "
        "vraagt, rond de reactie handmatig af en bevestig die hier."
    ),
    "DOCUMENT_UPLOAD_REQUIRED": (
        "Deze site vraagt documenten. Beoordeel zelf welke stukken passend zijn en upload niets "
        "zonder de inhoud en ontvanger te controleren. Rond de reactie handmatig af."
    ),
    "SENSITIVE_FIELD_REQUIRED": (
        "De site vraagt gevoelige of financiële gegevens. Controleer deze persoonlijk en reageer "
        "alleen handmatig; de agent vult zulke gegevens nooit zelfstandig in."
    ),
    "REGISTRATION_OR_PURCHASE_FLOW": (
        "Deze actie lijkt registratie, betaling of een andere bindende stap te starten. Controleer "
        "de volledige flow zelf en bevestig alleen een werkelijk verzonden woningreactie."
    ),
}

DEFAULT_INSTRUCTIONS = (
    "De siteflow kon niet veilig automatisch worden afgerond. Open de bron, controleer het "
    "conceptbericht en de gevraagde gegevens, verstuur de reactie handmatig en bevestig het resultaat."
)


class AssistanceService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def ensure_open(
        self,
        *,
        submission_id: int,
        listing_id: int,
        source_id: int,
        reason_code: str,
        summary: str,
    ) -> int:
        notify = False
        with SessionLocal() as db:
            request = db.scalar(
                select(AssistanceRequest).where(
                    AssistanceRequest.submission_id == submission_id
                )
            )
            if request is None:
                request = AssistanceRequest(
                    submission_id=submission_id,
                    listing_id=listing_id,
                    source_id=source_id,
                    state=AssistanceState.OPEN.value,
                    reason_code=reason_code,
                    summary=summary[:500],
                    instructions=INSTRUCTION_MAP.get(reason_code, DEFAULT_INSTRUCTIONS),
                )
                db.add(request)
                notify = True
            else:
                request.state = AssistanceState.OPEN.value
                request.reason_code = reason_code
                request.summary = summary[:500]
                request.instructions = INSTRUCTION_MAP.get(reason_code, DEFAULT_INSTRUCTIONS)
                request.resolved_at = None
                notify = request.notified_at is None
            add_audit(
                db,
                "USER_ASSISTANCE_REQUIRED",
                f"Handmatige hulp nodig: {reason_code}",
                listing_id=listing_id,
                source_id=source_id,
                data={"submission_id": submission_id, "reason_code": reason_code},
            )
            db.commit()
            request_id = request.id
        if notify and self._notify(request_id):
            with SessionLocal() as db:
                saved = db.get(AssistanceRequest, request_id)
                if saved:
                    saved.notified_at = datetime.now(UTC)
                    db.commit()
        return request_id

    def close_for_submission(self, submission_id: int) -> None:
        with SessionLocal() as db:
            request = db.scalar(
                select(AssistanceRequest).where(
                    AssistanceRequest.submission_id == submission_id,
                    AssistanceRequest.state == AssistanceState.OPEN.value,
                )
            )
            if request:
                request.state = AssistanceState.RESOLVED.value
                request.resolved_at = datetime.now(UTC)
                db.commit()

    def confirm_manual_submission(self, assistance_id: int, note: str = "") -> AssistanceOutcome:
        with SessionLocal() as db:
            request = db.get(AssistanceRequest, assistance_id)
            if request is None or request.state != AssistanceState.OPEN.value:
                return AssistanceOutcome(False, "ASSISTANCE_NOT_OPEN")
            submission = db.get(Submission, request.submission_id)
            listing = db.get(Listing, request.listing_id)
            if submission is None or listing is None:
                return AssistanceOutcome(False, "RELATED_RECORD_MISSING")
            now = datetime.now(UTC)
            submission.state = SubmissionState.SENT.value
            submission.completed_at = now
            submission.error_code = None
            submission.browser_result = {
                **(submission.browser_result or {}),
                "submitted": True,
                "method": "user_assisted",
            }
            listing.response_sent = True
            request.state = AssistanceState.RESOLVED.value
            request.resolved_at = now
            request.user_note = note.strip()[:500] or None
            add_audit(
                db,
                "REACTION_USER_CONFIRMED",
                "Gebruiker bevestigde dat de reactie handmatig is verzonden",
                listing_id=listing.id,
                source_id=request.source_id,
                data={"submission_id": submission.id, "assistance_id": request.id},
            )
            db.commit()
        return AssistanceOutcome(True, "USER_CONFIRMED")

    def skip(self, assistance_id: int, note: str = "") -> AssistanceOutcome:
        with SessionLocal() as db:
            request = db.get(AssistanceRequest, assistance_id)
            if request is None or request.state != AssistanceState.OPEN.value:
                return AssistanceOutcome(False, "ASSISTANCE_NOT_OPEN")
            listing = db.get(Listing, request.listing_id)
            if listing is None:
                return AssistanceOutcome(False, "LISTING_NOT_FOUND")
            request.state = AssistanceState.SKIPPED.value
            request.resolved_at = datetime.now(UTC)
            request.user_note = note.strip()[:500] or None
            listing.decision = Decision.IGNORE.value
            listing.reasoning_summary = "Handmatige reactie overgeslagen door de beheerder."
            add_audit(
                db,
                "REACTION_USER_SKIPPED",
                "Handmatige reactie overgeslagen",
                listing_id=listing.id,
                source_id=request.source_id,
                data={"assistance_id": request.id},
            )
            db.commit()
        return AssistanceOutcome(True, "USER_SKIPPED")

    def readiness(self) -> dict[str, Any]:
        blockers: list[str] = []
        with SessionLocal() as db:
            sources = db.scalars(select(SourceConfig)).all()
            open_count = db.scalar(
                select(AssistanceRequest.id)
                .where(AssistanceRequest.state == AssistanceState.OPEN.value)
                .limit(1)
            )
        auto_sources = [source for source in sources if source.mode == "AUTO_REACT" and source.enabled]
        if not self.settings.scheduler_enabled:
            blockers.append("Scheduler is uitgeschakeld")
        if self.settings.llm_provider == "disabled":
            blockers.append("LLM-provider is niet geconfigureerd")
        if not self.settings.auto_react_enabled:
            blockers.append("Globale automatische reacties zijn uitgeschakeld")
        if self.settings.dry_run:
            blockers.append("Dry-run is actief")
        if not auto_sources:
            blockers.append("Geen bron staat op AUTO_REACT")
        return {
            "ready": not blockers,
            "blockers": blockers,
            "auto_source_count": len(auto_sources),
            "open_assistance": bool(open_count),
        }

    def _notify(self, assistance_id: int) -> bool:
        if not (self.settings.telegram_bot_token and self.settings.telegram_chat_id):
            return False
        with SessionLocal() as db:
            config = db.get(SearchConfig, 1)
            if config is not None and not Criteria.model_validate(config.config).telegram_notify_assistance:
                return False
            request = db.get(AssistanceRequest, assistance_id)
            if request is None:
                return False
            listing = db.get(Listing, request.listing_id)
            source = db.get(SourceConfig, request.source_id)
            if listing is None or source is None:
                return False
            dashboard_url = f"{self.settings.public_base_url.rstrip('/')}/assistance"
            payload = {
                "chat_id": self.settings.telegram_chat_id,
                "text": (
                    f"<b>Hulp nodig bij woningreactie</b>\n{html.escape(listing.title)}\n"
                    f"Bron: {html.escape(source.display_name)}\n"
                    f"Reden: {html.escape(request.reason_code)}\n{html.escape(request.summary)}"
                ),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {
                    "inline_keyboard": [[
                        {"text": "Open bron", "url": listing.url},
                        {"text": "Help reageren", "url": dashboard_url},
                    ]]
                },
            }
        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage",
                json=payload,
                timeout=15,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("assistance notification failed")
            return False
        return True
