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
        sender = profile.sender_name.strip()
        joint = profile.message_perspective == "joint"
        opening = (
            f"Met veel interesse reageren wij op de woning aan {listing.address}."
            if joint
            else f"Met veel interesse reageer ik namens ons op de woning aan {listing.address}."
        )
        closing = applicants if joint else sender
        mandatory: list[str] = []
        if profile.always_include_financial and profile.financial_wording.strip():
            mandatory.append(profile.financial_wording.strip())
        if profile.always_include_guarantor and profile.guarantor_wording.strip():
            mandatory.append(profile.guarantor_wording.strip())
        mandatory.extend(item.strip() for item in profile.required_message_points if item.strip())

        def append_once(parts: list[str], text: str) -> None:
            if text and not any(text.casefold() in part.casefold() for part in parts):
                parts.append(text)

        if profile.standard_message.strip():
            parts = [
                "Beste verhuurder,",
                f"{opening} Vooral {specific} spreken ons aan.",
                profile.standard_message.strip(),
            ]
            for text in mandatory:
                append_once(parts, text)
            parts.extend(["Met vriendelijke groet,", closing])
            return "\n\n".join(parts)
        details = " ".join(profile.applicant_details)
        lifestyle = ", ".join(profile.lifestyle)
        other_applicants = ", ".join(name for name in profile.applicants if name != sender)
        identity = (
            f"Wij zijn {applicants}."
            if joint
            else (
                f"Mijn naam is {sender} en ik zoek samen met {other_applicants or 'mijn partner'} een woning."
            )
        )
        situation = (
            f"{identity} {profile.current_situation} {details} "
            f"We zijn {lifestyle} en zoeken {profile.desired_tenure}."
        )
        parts = [
            "Beste verhuurder,",
            f"{opening} Vooral {specific} spreken ons aan.",
            situation,
        ]
        for text in mandatory:
            append_once(parts, text)
        parts.extend(
            [
                "Ik hoor graag of we de woning mogen bezichtigen."
                if not joint
                else "Wij horen graag of we de woning mogen bezichtigen.",
                "Met vriendelijke groet,",
                closing,
            ]
        )
        return "\n\n".join(parts)
