from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.adapters import ALL_ADAPTERS, SourceAdapter
from app.config import get_settings
from app.db import SessionLocal
from app.models import ApplicantProfile, Decision, Listing, SearchConfig, SourceConfig
from app.schemas import ApplicantProfileData, Criteria, NormalizedListing, RuleResult
from app.services.audit import add_audit
from app.services.dedup import find_or_create_canonical
from app.services.llm import ListingLLMService, LLMRun
from app.services.response import DeterministicDutchResponseProvider
from app.services.rules import RuleEngine
from app.services.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

ADAPTERS: dict[str, type[SourceAdapter]] = {adapter.source_name: adapter for adapter in ALL_ADAPTERS}


class Pipeline:
    def __init__(self) -> None:
        settings = get_settings()
        self.rule_engine = RuleEngine()
        self.response_provider = DeterministicDutchResponseProvider()
        self.llm_service = ListingLLMService(settings)
        self.notifier = TelegramNotifier(settings)

    def run_source(self, source_name: str) -> dict[str, Any]:
        with SessionLocal() as db:
            source = db.scalar(select(SourceConfig).where(SourceConfig.name == source_name))
            if not source:
                raise ValueError(f"unknown source: {source_name}")
            if not source.enabled:
                return {"source": source_name, "skipped": "disabled"}
            adapter_type = ADAPTERS.get(source_name)
            if not adapter_type:
                raise ValueError(f"no adapter registered: {source_name}")
            source.last_started_at = datetime.now(UTC)
            add_audit(
                db,
                "SOURCE_RUN_STARTED",
                f"Fetch gestart voor {source.display_name}",
                source_id=source.id,
            )
            db.commit()

        try:
            discovered = adapter_type().discover()
            if not discovered:
                raise RuntimeError("adapter parsed zero listings; possible source drift")
        except Exception as exc:
            self._record_source_failure(source_name, exc)
            logger.exception("source run failed", extra={"context": {"source": source_name}})
            return {"source": source_name, "error": type(exc).__name__}

        created = 0
        updated = 0
        notified = 0
        item_errors = 0
        for normalized in discovered:
            try:
                outcome = self._process_listing(source_name, normalized)
                created += int(outcome["created"])
                updated += int(not outcome["created"])
                notified += int(outcome["notified"])
            except (ValidationError, IntegrityError, ValueError) as exc:
                item_errors += 1
                logger.warning(
                    "listing processing failed",
                    extra={
                        "context": {
                            "source": source_name,
                            "external_id": normalized.external_id,
                            "error": type(exc).__name__,
                        }
                    },
                )

        with SessionLocal() as db:
            source = db.scalar(select(SourceConfig).where(SourceConfig.name == source_name))
            assert source is not None
            source.last_success_at = datetime.now(UTC)
            source.last_error = None
            source.last_item_count = len(discovered)
            source.consecutive_failures = 0
            add_audit(
                db,
                "SOURCE_RUN_COMPLETED",
                f"{source.display_name}: {len(discovered)} advertenties verwerkt",
                source_id=source.id,
                data={"created": created, "updated": updated, "item_errors": item_errors},
            )
            db.commit()
        return {
            "source": source_name,
            "discovered": len(discovered),
            "created": created,
            "updated": updated,
            "notified": notified,
            "item_errors": item_errors,
        }

    def _process_listing(self, source_name: str, normalized: NormalizedListing) -> dict[str, bool]:
        with SessionLocal() as db:
            source = db.scalar(select(SourceConfig).where(SourceConfig.name == source_name))
            search_config = db.get(SearchConfig, 1)
            profile_record = db.get(ApplicantProfile, 1)
            if not source or not search_config or not profile_record:
                raise ValueError("application defaults are not seeded")
            criteria = Criteria.model_validate(search_config.config)
            profile = ApplicantProfileData.model_validate(profile_record.profile)
            existing = db.scalar(
                select(Listing).where(
                    Listing.source_id == source.id,
                    Listing.external_id == normalized.external_id,
                )
            )
            created = existing is None
            canonical = find_or_create_canonical(db, normalized)
            evaluation = self.rule_engine.evaluate(normalized, criteria)
            decision = evaluation.decision
            rule_results = list(evaluation.rules)
            summary = evaluation.summary
            draft: str | None = None
            llm_run: LLMRun | None = None
            content_hash = self._content_hash(normalized, profile)

            if decision is not Decision.IGNORE:
                deterministic_draft = self.response_provider.generate(normalized, profile)
                old_raw = existing.raw_data if existing and isinstance(existing.raw_data, dict) else {}
                if existing and existing.response_draft and old_raw.get("_content_hash") == content_hash:
                    draft = existing.response_draft
                else:
                    llm_run = self.llm_service.generate(normalized, profile, deterministic_draft)
                    draft = llm_run.draft
                    if self.llm_service.enabled:
                        decision, rule_results, summary = self._apply_llm_safety(
                            decision, rule_results, summary, llm_run
                        )

            listing = existing or Listing(
                source_id=source.id,
                canonical_property_id=canonical.id,
                external_id=normalized.external_id,
                url=str(normalized.url),
                title=normalized.title,
                address=normalized.address,
                city=normalized.city,
            )
            listing.canonical_property_id = canonical.id
            self._copy_normalized(listing, normalized)
            listing.decision = decision.value
            listing.match_score = evaluation.score
            listing.rule_results = [result.model_dump(mode="json") for result in rule_results]
            listing.reasoning_summary = summary
            listing.response_draft = draft
            listing.last_seen_at = datetime.now(UTC)
            listing.raw_data = {**listing.raw_data, "_content_hash": content_hash}
            if llm_run:
                listing.raw_data = {
                    **listing.raw_data,
                    "_llm": {
                        "provider": llm_run.provider,
                        "model": llm_run.model,
                        "error": llm_run.error,
                        "needs_review": llm_run.result.needs_review if llm_run.result else None,
                        "unusual_requirements": (
                            llm_run.result.unusual_requirements if llm_run.result else []
                        ),
                    },
                }
            if created:
                db.add(listing)
            db.flush()
            add_audit(
                db,
                "LISTING_DISCOVERED" if created else "LISTING_REFRESHED",
                f"{listing.title}: {listing.decision} ({listing.match_score}/100)",
                listing_id=listing.id,
                source_id=source.id,
                data={
                    "decision": listing.decision,
                    "score": listing.match_score,
                    "canonical_property_id": canonical.id,
                    "draft_created": bool(draft),
                    "llm_provider": llm_run.provider if llm_run else "cached-or-disabled",
                    "llm_model": llm_run.model if llm_run else None,
                    "llm_error": bool(llm_run and llm_run.error),
                },
            )
            db.commit()

            notification_sent = False
            if created and listing.decision != Decision.IGNORE.value:
                result = self.notifier.notify_listing(listing, source)
                notification_sent = bool(result.get("sent"))
                add_audit(
                    db,
                    "TELEGRAM_SENT" if notification_sent else "TELEGRAM_SKIPPED",
                    "Telegram-notificatie verzonden"
                    if notification_sent
                    else "Telegram niet geconfigureerd; notificatie overgeslagen",
                    listing_id=listing.id,
                    source_id=source.id,
                    data=result,
                )
                db.commit()
            return {"created": created, "notified": notification_sent}

    @staticmethod
    def _apply_llm_safety(
        decision: Decision,
        rules: list[RuleResult],
        summary: str,
        run: LLMRun,
    ) -> tuple[Decision, list[RuleResult], str]:
        if run.error or not run.result:
            rules.append(
                RuleResult(
                    rule="llm_analysis",
                    outcome="review",
                    detail="LLM niet beschikbaar; deterministische concepttekst gebruikt.",
                )
            )
            if decision is Decision.AUTO_REACT:
                decision = Decision.REVIEW
            return decision, rules, f"{summary} LLM-analyse mislukt; handmatige controle vereist."

        result = run.result
        llm_requires_review = (
            result.needs_review or result.suitable_for_two is not True or bool(result.unusual_requirements)
        )
        rules.append(
            RuleResult(
                rule="llm_analysis",
                outcome="review" if llm_requires_review else "pass",
                detail=result.explanation,
            )
        )
        if llm_requires_review and decision is Decision.AUTO_REACT:
            decision = Decision.REVIEW
        suffix = (
            "Handmatige controle vereist." if llm_requires_review else "Geen tekstuele blokkade gevonden."
        )
        return decision, rules, f"{summary} LLM: {result.explanation} {suffix}"

    @staticmethod
    def _content_hash(listing: NormalizedListing, profile: ApplicantProfileData) -> str:
        profile_payload = profile.model_dump(mode="json")
        profile_fingerprint = hashlib.sha256(
            json.dumps(profile_payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        relevant = {
            "title": listing.title,
            "rent_total": str(listing.rent_total),
            "area_m2": str(listing.area_m2),
            "bedrooms": listing.bedrooms,
            "rooms": listing.rooms,
            "description": listing.description,
            "availability": listing.availability_text,
            "is_available": listing.is_available,
            "profile_fingerprint": profile_fingerprint,
        }
        payload = json.dumps(relevant, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _copy_normalized(listing: Listing, normalized: NormalizedListing) -> None:
        listing.url = str(normalized.url)
        listing.title = normalized.title
        listing.address = normalized.address
        listing.postcode = normalized.postcode
        listing.city = normalized.city
        listing.property_type = normalized.property_type
        listing.rent_base = normalized.rent_base
        listing.service_costs = normalized.service_costs
        listing.rent_total = normalized.rent_total
        listing.area_m2 = normalized.area_m2
        listing.bedrooms = normalized.bedrooms
        listing.rooms = normalized.rooms
        listing.description = normalized.description
        listing.availability_text = normalized.availability_text
        listing.is_available = normalized.is_available
        listing.image_url = str(normalized.image_url) if normalized.image_url else None
        listing.raw_data = normalized.raw_data

    @staticmethod
    def _record_source_failure(source_name: str, exc: Exception) -> None:
        with SessionLocal() as db:
            source = db.scalar(select(SourceConfig).where(SourceConfig.name == source_name))
            if not source:
                return
            source.last_error_at = datetime.now(UTC)
            source.last_error = f"{type(exc).__name__}: {str(exc)[:800]}"
            source.consecutive_failures += 1
            add_audit(
                db,
                "SOURCE_RUN_FAILED",
                f"{source.display_name} mislukt: {type(exc).__name__}",
                source_id=source.id,
                data={"error_type": type(exc).__name__},
            )
            db.commit()

    def run_all(self) -> list[dict[str, Any]]:
        with SessionLocal() as db:
            names = db.scalars(select(SourceConfig.name).where(SourceConfig.enabled.is_(True))).all()
        return [self.run_source(name) for name in names]
