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
            previous_failures = source.consecutive_failures
            source.last_started_at = datetime.now(UTC)
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
            if created or item_errors or previous_failures:
                add_audit(
                    db,
                    (
                        "SOURCE_RUN_RECOVERED"
                        if previous_failures and not item_errors
                        else "SOURCE_RUN_COMPLETED"
                    ),
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
            previous_decision = existing.decision if existing else None
            previous_available = existing.is_available if existing else None
            old_raw = existing.raw_data if existing and isinstance(existing.raw_data, dict) else {}
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
                can_reuse_draft = bool(
                    existing
                    and existing.response_draft
                    and old_raw.get("_content_hash") == content_hash
                    and (not self.llm_service.enabled or isinstance(old_raw.get("_llm"), dict))
                )
                if can_reuse_draft:
                    assert existing is not None and existing.response_draft is not None
                    draft = existing.response_draft
                    if self.llm_service.enabled:
                        decision, rule_results, summary = self._apply_cached_llm_safety(
                            decision,
                            rule_results,
                            summary,
                            old_raw.get("_llm"),
                            criteria,
                        )
                else:
                    llm_run = self.llm_service.generate(normalized, profile, deterministic_draft)
                    draft = llm_run.draft
                    if self.llm_service.enabled:
                        decision, rule_results, summary = self._apply_llm_safety(
                            decision, rule_results, summary, llm_run, criteria
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
            if normalized.is_available and decision is not Decision.IGNORE:
                listing.archived_at = None
            listing.match_score = evaluation.score
            listing.rule_results = [result.model_dump(mode="json") for result in rule_results]
            listing.reasoning_summary = summary
            listing.response_draft = draft
            listing.last_seen_at = datetime.now(UTC)
            internal_data: dict[str, Any] = {
                "_content_hash": content_hash,
            }
            if isinstance(old_raw.get("_llm"), dict):
                internal_data["_llm"] = old_raw["_llm"]
            if llm_run and self.llm_service.enabled:
                internal_data["_llm"] = {
                    "provider": llm_run.provider,
                    "model": llm_run.model,
                    "error": llm_run.error,
                    "needs_review": llm_run.result.needs_review if llm_run.result else None,
                    "suitable_for_two": (
                        llm_run.result.suitable_for_two if llm_run.result else None
                    ),
                    "explanation": llm_run.result.explanation if llm_run.result else None,
                    "unusual_requirements": (llm_run.result.unusual_requirements if llm_run.result else []),
                }
            listing.raw_data = {**normalized.raw_data, **internal_data}
            if created:
                db.add(listing)
            db.flush()
            materially_changed = (
                created
                or old_raw.get("_content_hash") != content_hash
                or previous_decision != listing.decision
                or previous_available != listing.is_available
            )
            if materially_changed:
                add_audit(
                    db,
                    "LISTING_DISCOVERED" if created else "LISTING_CHANGED",
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
                result = self.notifier.notify_listing(listing, source, criteria)
                notification_sent = bool(result.get("sent"))
                skipped_reason = result.get("reason")
                add_audit(
                    db,
                    "TELEGRAM_SENT" if notification_sent else "TELEGRAM_SKIPPED",
                    "Telegram-notificatie verzonden"
                    if notification_sent
                    else (
                        "Telegram-filter heeft deze advertentie overgeslagen"
                        if skipped_reason == "listing_filter"
                        else "Telegram niet beschikbaar; notificatie overgeslagen"
                    ),
                    listing_id=listing.id,
                    source_id=source.id,
                    data=result,
                )
                db.commit()
            return {
                "created": created,
                "notified": notification_sent,
                "became_auto_react": (
                    listing.decision == Decision.AUTO_REACT.value
                    and previous_decision != Decision.AUTO_REACT.value
                ),
            }

    @staticmethod
    def _apply_cached_llm_safety(
        decision: Decision,
        rules: list[RuleResult],
        summary: str,
        raw_meta: object,
        criteria: Criteria,
    ) -> tuple[Decision, list[RuleResult], str]:
        fast = criteria.auto_react_aggressiveness == "fast"
        if not isinstance(raw_meta, dict):
            if decision is Decision.AUTO_REACT and not fast:
                decision = Decision.REVIEW
            rules.append(
                RuleResult(
                    rule="llm_analysis",
                    outcome="review",
                    detail=(
                        "Eerdere AI-controle ontbreekt; snelle modus gebruikt de harde regels."
                        if fast
                        else "Eerdere AI-veiligheidscontrole ontbreekt; opnieuw beoordelen vereist."
                    ),
                )
            )
            return decision, rules, f"{summary} Eerdere AI-controle ontbreekt."

        unusual = raw_meta.get("unusual_requirements")
        unusual_requirements = unusual if isinstance(unusual, list) else []
        needs_review = (
            bool(raw_meta.get("error"))
            or raw_meta.get("needs_review") is not False
            or bool(unusual_requirements)
        )
        explanation = raw_meta.get("explanation")
        detail = (
            str(explanation)
            if explanation
            else (
                "De opgeslagen AI-controle vereist handmatige beoordeling."
                if needs_review
                else "De opgeslagen AI-controle bevat geen tekstuele blokkade."
            )
        )
        rules.append(
            RuleResult(
                rule="llm_analysis",
                outcome="review" if needs_review else "pass",
                detail=detail,
            )
        )
        suitable_for_two = raw_meta.get("suitable_for_two")
        hard_llm_block = suitable_for_two is False
        if (hard_llm_block or (needs_review and not fast)) and decision is Decision.AUTO_REACT:
            decision = Decision.REVIEW
        suffix = (
            "Snelle modus laat deze onzekerheid niet wachten."
            if needs_review and fast and not hard_llm_block
            else "Handmatige controle vereist."
            if needs_review
            else "Geen tekstuele blokkade gevonden."
        )
        return decision, rules, f"{summary} AI (opgeslagen): {detail} {suffix}"

    @staticmethod
    def _apply_llm_safety(
        decision: Decision,
        rules: list[RuleResult],
        summary: str,
        run: LLMRun,
        criteria: Criteria,
    ) -> tuple[Decision, list[RuleResult], str]:
        fast = criteria.auto_react_aggressiveness == "fast"
        if run.error or not run.result:
            rules.append(
                RuleResult(
                    rule="llm_analysis",
                    outcome="review",
                    detail=(
                        "LLM niet beschikbaar; snelle modus gebruikt het vaste bericht en harde regels."
                        if fast
                        else "LLM niet beschikbaar; deterministische concepttekst gebruikt."
                    ),
                )
            )
            if decision is Decision.AUTO_REACT and not fast:
                decision = Decision.REVIEW
            suffix = (
                "Snelle modus wacht niet op de LLM."
                if fast
                else "LLM-analyse mislukt; handmatige controle vereist."
            )
            return decision, rules, f"{summary} {suffix}"

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
        hard_llm_block = result.suitable_for_two is False
        if (hard_llm_block or (llm_requires_review and not fast)) and decision is Decision.AUTO_REACT:
            decision = Decision.REVIEW
        suffix = (
            "Snelle modus laat deze onzekerheid niet wachten."
            if llm_requires_review and fast and not hard_llm_block
            else "Handmatige controle vereist."
            if llm_requires_review
            else "Geen tekstuele blokkade gevonden."
        )
        return decision, rules, f"{summary} LLM: {result.explanation} {suffix}"

    @staticmethod
    def _content_hash(listing: NormalizedListing, profile: ApplicantProfileData) -> str:
        profile_payload = profile.model_dump(mode="json")
        profile_fingerprint = hashlib.sha256(
            json.dumps(profile_payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        relevant = {
            "draft_version": 2,
            "title": listing.title,
            "rent_total": str(listing.rent_total),
            "area_m2": str(listing.area_m2),
            "bedrooms": listing.bedrooms,
            "rooms": listing.rooms,
            "description": listing.description,
            "availability": listing.availability_text,
            "published_at": listing.published_at.isoformat() if listing.published_at else None,
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
        if normalized.published_at is not None:
            listing.published_at = normalized.published_at
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
