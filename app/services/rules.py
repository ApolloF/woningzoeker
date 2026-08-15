from __future__ import annotations

import re

from app.models import Decision
from app.schemas import Criteria, Evaluation, NormalizedListing, RuleResult


class RuleEngine:
    ONE_PERSON_PATTERN = re.compile(
        r"(uitsluitend|alleen|geschikt voor|maximaal)\s+(?:één|1)\s+persoon|"
        r"(?:één|1)\s+persoons?(?:woning|huishouden)",
        re.I,
    )
    AMBIGUOUS_REQUIREMENT_PATTERN = re.compile(
        r"(inkomenseis|bruto\s+(?:maand)?inkomen|jaarrekening|creditcheck|id-check|"
        r"expats?\s+(?:only|uitsluitend)|short[- ]?stay|tijdelijk contract|diplomatenclausule|"
        r"identiteitsbewijs|paspoort|arbeidscontract)",
        re.I,
    )

    def evaluate(self, listing: NormalizedListing, criteria: Criteria) -> Evaluation:
        score = 50
        rules: list[RuleResult] = []
        description = listing.description or ""

        if not listing.is_available:
            return self._ignored("availability", "Listing is niet meer beschikbaar.")
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
        if listing.rent_total is None:
            rules.append(RuleResult(rule="price", outcome="unknown", detail="Totale huur ontbreekt"))
        elif listing.rent_total <= criteria.target_total_monthly:
            score += 20
            price_ok = True
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
                RuleResult(
                    rule="area", outcome="pass", detail=f"{listing.area_m2} m²", score_delta=15
                )
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

        score = max(0, min(100, score))
        auto_safe = price_ok and area_ok and accepted_type and score >= 75 and not ambiguous
        decision = Decision.AUTO_REACT if auto_safe else Decision.REVIEW
        summary = (
            "Voldoet aan de harde, deterministische criteria."
            if decision is Decision.AUTO_REACT
            else "Een of meer gegevens/voorkeuren vereisen beoordeling."
        )
        return Evaluation(decision=decision, score=score, rules=rules, summary=summary)

    @staticmethod
    def _ignored(rule: str, detail: str) -> Evaluation:
        return Evaluation(
            decision=Decision.IGNORE,
            score=0,
            rules=[RuleResult(rule=rule, outcome="fail", detail=detail, score_delta=-100)],
            summary=detail,
        )
