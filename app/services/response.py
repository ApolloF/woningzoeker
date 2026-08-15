from __future__ import annotations

import re
from typing import Protocol

from app.schemas import ApplicantProfileData, NormalizedListing


class ResponseProvider(Protocol):
    def generate(self, listing: NormalizedListing, profile: ApplicantProfileData) -> str: ...

    def apply_word_limit(
        self, draft: str, max_words: int | None, profile: ApplicantProfileData
    ) -> str: ...


class DeterministicDutchResponseProvider:
    def generate(self, listing: NormalizedListing, profile: ApplicantProfileData) -> str:
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
            normalized = self._normalized_text(text)
            if text and not any(normalized in self._normalized_text(part) for part in parts):
                parts.append(text)

        if profile.standard_message.strip():
            letter = self._complete_letter(profile.standard_message.strip(), mandatory)
            return self.polish_dutch(letter)
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
            f"{opening} De woning spreekt ons erg aan.",
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
        return self.polish_dutch("\n\n".join(parts))

    @staticmethod
    def polish_dutch(letter: str) -> str:
        """Apply narrow, deterministic fixes without rewriting the applicant's voice."""
        polished = re.sub(
            r"\b([A-ZÀ-ÖØ-Ý][\wÀ-ÿ'-]+\s+en\s+ik(?:\s+samen)?)\s+woon\b",
            r"\1 wonen",
            letter,
            flags=re.I,
        )
        polished = re.sub(
            r",\s*met\s+\d+(?:[.,]\d+)?\s*m[²2]\s+en\s+\d+\s+kamers?\s*,",
            ",",
            polished,
            flags=re.I,
        )
        polished = re.sub(
            r"(?im)^\s*Vooral\s+de\s+woonoppervlakte\s+van\s+[^.\n]+"
            r"(?:kamer|kamers)[^.\n]*spre(?:ekt|ken)\s+ons\s+aan\.\s*",
            "",
            polished,
        )
        polished = re.sub(r"[ \t]+([,.!?])", r"\1", polished)
        return re.sub(r"\n{3,}", "\n\n", polished).strip()

    @classmethod
    def apply_word_limit(
        cls,
        draft: str,
        max_words: int | None,
        profile: ApplicantProfileData,
    ) -> str:
        if max_words is None or len(draft.split()) <= max_words:
            return draft
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", draft) if part.strip()]
        required = [item.strip() for item in profile.required_message_points if item.strip()]
        if profile.always_include_financial and profile.financial_wording.strip():
            required.append(profile.financial_wording.strip())
        if profile.always_include_guarantor and profile.guarantor_wording.strip():
            required.append(profile.guarantor_wording.strip())
        required_normalized = [cls._normalized_text(item) for item in required]
        protected: set[int] = set()
        closing_seen = False
        for index, paragraph in enumerate(paragraphs):
            normalized = cls._normalized_text(paragraph)
            if index == 0 and re.search(r"\bbeste\b", normalized):
                protected.add(index)
            if any(item in normalized for item in required_normalized):
                protected.add(index)
            if re.search(r"\bmet vriendelijke groet\b", normalized):
                closing_seen = True
            if closing_seen:
                protected.add(index)

        selected = set(protected)
        used = sum(len(paragraphs[index].split()) for index in protected)
        for index, paragraph in enumerate(paragraphs):
            if index in selected:
                continue
            words = len(paragraph.split())
            if used + words <= max_words:
                selected.add(index)
                used += words
                continue
            remaining = max_words - used
            if remaining < 5:
                continue
            sentences = re.split(r"(?<=[.!?])\s+", paragraph)
            kept: list[str] = []
            for sentence in sentences:
                sentence_words = len(sentence.split())
                if sentence_words <= remaining:
                    kept.append(sentence)
                    remaining -= sentence_words
            if kept:
                paragraphs[index] = " ".join(kept)
                selected.add(index)
                used = max_words - remaining
        return "\n\n".join(
            paragraph for index, paragraph in enumerate(paragraphs) if index in selected
        )

    @staticmethod
    def _complete_letter(letter: str, mandatory: list[str]) -> str:
        normalized_letter = DeterministicDutchResponseProvider._normalized_text(letter)
        missing = [
            item
            for item in mandatory
            if item
            and DeterministicDutchResponseProvider._normalized_text(item) not in normalized_letter
        ]
        if not missing:
            return letter
        addition = "\n\n".join(missing)
        closing = re.search(r"(?im)^\s*met vriendelijke groet\s*,?\s*$", letter)
        if closing is None:
            return f"{letter.rstrip()}\n\n{addition}"
        return f"{letter[: closing.start()].rstrip()}\n\n{addition}\n\n{letter[closing.start() :].lstrip()}"

    @staticmethod
    def _normalized_text(value: str) -> str:
        return " ".join(value.casefold().split())
