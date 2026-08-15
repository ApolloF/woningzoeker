from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Listing, SourceConfig
from app.schemas import NormalizedListing
from app.services.assisted_reactions import AssistedReactionService
from app.services.audit import add_audit
from app.services.pipeline_base import Pipeline as BasePipeline


class Pipeline(BasePipeline):
    """Listing pipeline with immediate, idempotent reaction dispatch for new matches."""

    def __init__(self) -> None:
        super().__init__()
        self.settings = get_settings()
        self.reaction_service = AssistedReactionService(self.settings)

    def run_source(self, source_name: str) -> dict[str, Any]:
        outcome = super().run_source(source_name)
        if "error" not in outcome:
            return outcome
        with SessionLocal() as db:
            source = db.scalar(select(SourceConfig).where(SourceConfig.name == source_name))
            if source is None or source.consecutive_failures != 3:
                return outcome
            result = self.notifier.notify_source_failure(source)
            add_audit(
                db,
                "SOURCE_FAILURE_ALERT_SENT" if result.get("sent") else "SOURCE_FAILURE_ALERT_SKIPPED",
                f"{source.display_name}: waarschuwing na drie mislukte controles",
                source_id=source.id,
                data=result,
            )
            db.commit()
        return outcome

    def _process_listing(self, source_name: str, normalized: NormalizedListing) -> dict[str, bool]:
        outcome = super()._process_listing(source_name, normalized)
        if (
            not outcome["created"]
            and not outcome.get("became_auto_react", False)
        ) or self.settings.llm_provider == "disabled":
            return outcome
        with SessionLocal() as db:
            listing = db.scalar(
                select(Listing)
                .join(SourceConfig, SourceConfig.id == Listing.source_id)
                .where(
                    SourceConfig.name == source_name,
                    Listing.external_id == normalized.external_id,
                )
            )
        if listing is None:
            return outcome
        llm_meta = listing.raw_data.get("_llm", {}) if isinstance(listing.raw_data, dict) else {}
        if (
            not isinstance(llm_meta, dict)
            or llm_meta.get("error")
            or llm_meta.get("needs_review") is not False
        ):
            return outcome
        dispatch = self.reaction_service.dispatch(listing.id)
        outcome["reacted"] = dispatch.status == "sent"
        return outcome

    def retry_failed_reactions(self) -> list[dict[str, object]]:
        return [result.as_dict() for result in self.reaction_service.retry_failed()]
