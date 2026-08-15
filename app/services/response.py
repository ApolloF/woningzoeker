from __future__ import annotations

from typing import Protocol

from app.schemas import ApplicantProfileData, NormalizedListing


class ResponseProvider(Protocol):
    def generate(self, listing: NormalizedListing, profile: ApplicantProfileData) -> str: ...


class DeterministicDutchResponseProvider:
    def generate(self, listing: NormalizedListing, profile: ApplicantProfileData) -> str:
        characteristics: list[str] = []
        if listing.area_m2:
            characteristics.append(f"de woonoppervlakte van {listing.area_m2:g} m²")
        if listing.bedrooms:
            label = "slaapkamer" if listing.bedrooms == 1 else "slaapkamers"
            characteristics.append(f"de {listing.bedrooms} {label}")
        if len(characteristics) < 2:
            characteristics.append(f"de ligging in {listing.city}")
        specific = " en ".join(characteristics[:2])

        applicants = " en ".join(profile.applicants)
        if profile.standard_message.strip():
            return (
                "Beste verhuurder,\n\n"
                f"Met veel interesse reageren wij op de woning aan {listing.address}. "
                f"Vooral {specific} spreken ons aan.\n\n"
                f"{profile.standard_message.strip()}\n\n"
                f"Met vriendelijke groet,\n{applicants}"
            )
        details = " ".join(profile.applicant_details)
        lifestyle = ", ".join(profile.lifestyle)
        return (
            "Beste verhuurder,\n\n"
            f"Met veel interesse reageren wij op de woning aan {listing.address}. "
            f"Vooral {specific} spreken ons aan.\n\n"
            f"Wij zijn {applicants}. {profile.current_situation} {details} "
            f"We zijn {lifestyle} en zoeken {profile.desired_tenure}.\n\n"
            f"{profile.financial_wording}\n\n"
            "Wij horen graag of we de woning mogen bezichtigen.\n\n"
            f"Met vriendelijke groet,\n{applicants}"
        )
