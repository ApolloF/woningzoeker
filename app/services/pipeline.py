from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Decision, Listing, SearchConfig, SourceConfig, SourceMode, Submission
from app.schemas import Criteria, NormalizedListing
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
            config = db.get(SearchConfig, 1)
            criteria = Criteria.model_validate(config.config) if config else Criteria()
            if not criteria.telegram_notify_source_failures:
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
            not outcome["created"] and not outcome.get("became_auto_react", False)
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
        if listing.decision != Decision.AUTO_REACT.value:
            return outcome
        dispatch = self.reaction_service.dispatch(listing.id)
        outcome["reacted"] = dispatch.status == "sent"
        if dispatch.status == "sent":
            self._notify_reaction_sent(listing.id)
        return outcome

    def retry_failed_reactions(self) -> list[dict[str, object]]:
        return [result.as_dict() for result in self.reaction_service.retry_failed()]

    def dispatch_pending_auto_reactions(self) -> list[dict[str, object]]:
        """Pick up fresh eligible offers after mode changes or process restarts."""
        with SessionLocal() as db:
            config = db.get(SearchConfig, 1)
            if config is None:
                return []
            criteria = Criteria.model_validate(config.config)
            query = (
                select(Listing.id)
                .join(SourceConfig, SourceConfig.id == Listing.source_id)
                .outerjoin(Submission, Submission.canonical_property_id == Listing.canonical_property_id)
                .where(
                    Listing.decision == Decision.AUTO_REACT.value,
                    Listing.is_available.is_(True),
                    Listing.archived_at.is_(None),
                    SourceConfig.enabled.is_(True),
                    SourceConfig.mode == SourceMode.AUTO_REACT.value,
                    Submission.id.is_(None),
                )
                .order_by(func.coalesce(Listing.published_at, Listing.first_seen_at).desc())
                .limit(10)
            )
            if criteria.max_listing_age_minutes is not None:
                cutoff = datetime.now(UTC) - timedelta(minutes=criteria.max_listing_age_minutes)
                query = query.where(func.coalesce(Listing.published_at, Listing.first_seen_at) >= cutoff)
            listing_ids = list(db.scalars(query).all())

        results: list[dict[str, object]] = []
        for listing_id in listing_ids:
            result = self.reaction_service.dispatch(listing_id)
            results.append(result.as_dict())
            if result.status == "sent":
                self._notify_reaction_sent(listing_id)
        return results

    def _notify_reaction_sent(self, listing_id: int) -> None:
        with SessionLocal() as db:
            listing = db.get(Listing, listing_id)
            config = db.get(SearchConfig, 1)
            if listing is None or config is None:
                return
            source = db.get(SourceConfig, listing.source_id)
            criteria = Criteria.model_validate(config.config)
            if source is None or not criteria.telegram_notify_sent_reactions:
                return
            result = self.notifier.notify_reaction_sent(listing, source)
            add_audit(
                db,
                "TELEGRAM_REACTION_SENT" if result.get("sent") else "TELEGRAM_REACTION_SKIPPED",
                "Telegram-status na automatische reactie",
                listing_id=listing.id,
                source_id=source.id,
                data=result,
            )
            db.commit()
