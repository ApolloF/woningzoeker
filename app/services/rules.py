from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from app.models import Decision
from app.schemas import Criteria, Evaluation, NormalizedListing, RuleResult


class RuleEngine:
    ONE_PERSON_PATTERN = re.compile(
        r"(uitsluitend|alleen|geschikt voor|maximaal)\s+(?:één|1)\s+persoon|"
        r"(?:één|1)\s+persoons?(?:woning|huishouden)",
        re.I,
    )
    AMBIGUOUS_REQUIREMENT_PATTERN = re.compile(
        r"(jaarrekening|creditcheck|id-check|"
        r"expats?\s+(?:only|uitsluitend)|short[- ]?stay|tijdelijk contract|diplomatenclausule|"
        r"identiteitsbewijs|paspoort|arbeidscontract)",
        re.I,
    )
    HARD_INCOME_PATTERN = re.compile(
        r"(?:inkomenseis|(?:bruto|netto)\s*(?:maand|jaar)?inkomen|vast\s+inkomen|"
        r"minimaal\s+(?:een\s+)?inkomen|"
        r"(?:minimaal|ten minste|tenminste)\s+\d+(?:[.,]\d+)?\s*x\s*(?:de\s*)?huur)",
        re.I,
    )
    INCOME_AMOUNT_PATTERN = re.compile(
        r"(?:€|eur(?:o)?\.?\s*)\s*(\d{1,3}(?:[.\s]\d{3})+|\d+)(?:[,.](\d{1,2}))?",
        re.I,
    )

    def evaluate(self, listing: NormalizedListing, criteria: Criteria) -> Evaluation:
        score = 50
        rules: list[RuleResult] = []
        description = listing.description or ""
        age_minutes: int | None = None

        if not listing.is_available:
            return self._ignored("availability", "Listing is niet meer beschikbaar.")
        if listing.published_at is not None:
            published_at = listing.published_at
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=UTC)
            age_minutes = max(0, int((datetime.now(UTC) - published_at).total_seconds() // 60))
            if (
                criteria.max_listing_age_minutes is not None
                and age_minutes > criteria.max_listing_age_minutes
            ):
                return self._ignored(
                    "listing_age",
                    f"Advertentie is {age_minutes} minuten oud; grens is {criteria.max_listing_age_minutes}.",
                )
            rules.append(
                RuleResult(
                    rule="listing_age",
                    outcome="pass",
                    detail=f"Ongeveer {age_minutes} minuten oud",
                )
            )
        if criteria.suitable_for_two_required and self.ONE_PERSON_PATTERN.search(description):
            return self._ignored("household_size", "Advertentie staat expliciet slechts één persoon toe.")
        if not criteria.allow_home_swap and "woningruil" in description.lower():
            return self._ignored("home_swap", "Woningruil is niet toegestaan in de zoekcriteria.")

        property_type = (listing.property_type or "").lower()
        if property_type == "kamer" and not criteria.allow_shared_rooms:
            return self._ignored("shared_room", "Kamers zijn uitgeschakeld.")

        city_ok = listing.city.lower() in {city.lower() for city in criteria.accepted_cities}
        if not city_ok:
            return self._ignored("location", f"{listing.city} valt buiten de ingestelde plaatsen.")
        score += 10
        rules.append(RuleResult(rule="location", outcome="pass", detail=listing.city, score_delta=10))

        accepted_type = property_type in {item.lower() for item in criteria.accepted_property_types}
        if accepted_type:
            score += 10
            rules.append(
                RuleResult(
                    rule="property_type",
                    outcome="pass",
                    detail=listing.property_type or "onbekend",
                    score_delta=10,
                )
            )
        else:
            rules.append(
                RuleResult(
                    rule="property_type",
                    outcome="unknown" if not property_type else "review",
                    detail=listing.property_type or "Woningtype ontbreekt",
                )
            )

        price_ok = False
        price_within_margin = False
        if listing.rent_total is None:
            rules.append(RuleResult(rule="price", outcome="unknown", detail="Totale huur ontbreekt"))
        elif listing.rent_total <= criteria.target_total_monthly:
            score += 20
            price_ok = True
            price_within_margin = True
            rules.append(
                RuleResult(
                    rule="price",
                    outcome="pass",
                    detail=f"€ {listing.rent_total} totaal",
                    score_delta=20,
                )
            )
        elif listing.rent_total <= criteria.target_total_monthly + criteria.soft_price_margin:
            score += 5
            price_within_margin = True
            rules.append(
                RuleResult(
                    rule="price",
                    outcome="review",
                    detail="Boven richtbedrag maar binnen zachte marge",
                    score_delta=5,
                )
            )
        else:
            score -= 25
            rules.append(
                RuleResult(rule="price", outcome="review", detail="Ruim boven richtbedrag", score_delta=-25)
            )

        area_ok = False
        if listing.area_m2 is None:
            rules.append(RuleResult(rule="area", outcome="unknown", detail="Oppervlakte ontbreekt"))
        elif listing.area_m2 >= criteria.min_area_m2:
            score += 15
            area_ok = True
            rules.append(
                RuleResult(rule="area", outcome="pass", detail=f"{listing.area_m2} m²", score_delta=15)
            )
        else:
            score -= 15
            rules.append(
                RuleResult(rule="area", outcome="review", detail="Kleiner dan voorkeur", score_delta=-15)
            )

        ambiguous = bool(self.AMBIGUOUS_REQUIREMENT_PATTERN.search(description))
        if ambiguous:
            rules.append(
                RuleResult(
                    rule="textual_requirements",
                    outcome="review",
                    detail="Advertentie bevat selectie-, inkomens- of contractvoorwaarden.",
                )
            )

        income_amount: Decimal | None = None
        hard_income_requirement = criteria.review_hard_income_requirements and bool(
            self.HARD_INCOME_PATTERN.search(description)
        )
        if hard_income_requirement:
            income_amount = self._income_amount(description)
            detail = "Advertentie bevat een harde inkomenseis."
            if income_amount is not None:
                detail = f"Advertentie noemt een inkomenseis van circa € {income_amount:g}."
                if (
                    criteria.max_required_monthly_income is not None
                    and income_amount > criteria.max_required_monthly_income
                ):
                    detail += " Dit ligt boven jouw ingestelde grens."
            rules.append(RuleResult(rule="hard_income_requirement", outcome="review", detail=detail))

        score = max(0, min(100, score))
        income_above_limit = bool(
            hard_income_requirement
            and income_amount is not None
            and criteria.max_required_monthly_income is not None
            and income_amount > criteria.max_required_monthly_income
        )
        if criteria.auto_react_aggressiveness == "fast":
            auto_safe = (
                price_within_margin
                and score >= criteria.auto_react_min_score
                and not income_above_limit
            )
        else:
            required_score = criteria.auto_react_min_score
            if criteria.auto_react_aggressiveness == "careful":
                required_score = max(85, required_score)
            auto_safe = (
                price_ok
                and area_ok
                and accepted_type
                and score >= required_score
                and not ambiguous
                and not hard_income_requirement
            )
        decision = Decision.AUTO_REACT if auto_safe else Decision.REVIEW
        if (
            decision is Decision.AUTO_REACT
            and age_minutes is not None
            and criteria.max_auto_react_age_minutes is not None
            and age_minutes > criteria.max_auto_react_age_minutes
        ):
            decision = Decision.REVIEW
            rules.append(
                RuleResult(
                    rule="auto_react_age",
                    outcome="review",
                    detail=(
                        f"Advertentie is {age_minutes} minuten oud; automatisch reageren stopt "
                        f"na {criteria.max_auto_react_age_minutes} minuten."
                    ),
                )
            )
        summary = (
            "Voldoet aan de harde, deterministische criteria."
            if decision is Decision.AUTO_REACT
            else "Een of meer gegevens/voorkeuren vereisen beoordeling."
        )
        return Evaluation(decision=decision, score=score, rules=rules, summary=summary)

    @classmethod
    def _income_amount(cls, description: str) -> Decimal | None:
        match = cls.INCOME_AMOUNT_PATTERN.search(description)
        if not match:
            return None
        whole = match.group(1).replace(".", "").replace(" ", "")
        cents = (match.group(2) or "0").ljust(2, "0")
        try:
            return Decimal(f"{whole}.{cents}")
        except InvalidOperation:
            return None

    @staticmethod
    def _ignored(rule: str, detail: str) -> Evaluation:
        return Evaluation(
            decision=Decision.IGNORE,
            score=0,
            rules=[RuleResult(rule=rule, outcome="fail", detail=detail, score_delta=-100)],
            summary=detail,
        )
