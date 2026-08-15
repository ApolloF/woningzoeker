from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import ALL_ADAPTERS
from app.models import ApplicantProfile, SearchConfig, SourceConfig, SourceMode
from app.schemas import ApplicantProfileData, Criteria

INITIAL_PROFILE = ApplicantProfileData(
    applicants=["Florian Greeven", "Sara Hutchinson"],
    current_city="Groningen",
    current_situation=(
        "We wonen samen aan het Gedempte Zuiderdiep en zoeken een andere woning omdat het "
        "huidige gebouw wordt verkocht."
    ),
    applicant_details=[
        "Florian studeert aan de Rijksuniversiteit Groningen en werkt als zelfstandig IT-professional.",
        "Sara heeft recent de Research Master Archaeology afgerond en werkt parttime als "
        "faculteitssecretaris bij de Rijksuniversiteit Groningen.",
    ],
    financial_wording=(
        "Financieel beschikken we gezamenlijk doorgaans over circa €4.500 per maand. Daarnaast "
        "heb ik voldoende spaargeld en beleggingen, waardoor er waar nodig ruim aanvullende "
        "financiële ruimte is."
    ),
    guarantor_wording=(
        "Mocht er een garantsteller worden gevraagd, dan is dat ook geen probleem: mijn oom kan "
        "als financieel zeer draagkrachtige garantsteller voor ons garant staan."
    ),
    lifestyle=["niet-rokers", "zonder huisdieren", "rustig en netjes"],
    desired_tenure="een prettige plek om voor langere tijd samen te wonen",
)


def seed_defaults(db: Session) -> None:
    for adapter in ALL_ADAPTERS:
        existing = db.scalar(select(SourceConfig).where(SourceConfig.name == adapter.source_name))
        if existing:
            continue
        db.add(
            SourceConfig(
                name=adapter.source_name,
                display_name=adapter.display_name,
                base_url=adapter.search_url,
                poll_interval_seconds=60,
                mode=SourceMode.DRAFT_ONLY.value,
                enabled=True,
            )
        )
    if not db.get(SearchConfig, 1):
        db.add(SearchConfig(id=1, config=Criteria().model_dump(mode="json")))
    if not db.get(ApplicantProfile, 1):
        db.add(ApplicantProfile(id=1, profile=INITIAL_PROFILE.model_dump(mode="json")))
    db.commit()
