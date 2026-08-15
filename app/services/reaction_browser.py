from __future__ import annotations

import re

from playwright.sync_api import Page

from app.services.reaction_browser_base import (
    REACTION_SPECS,
    BrowserReactionResult,
    ReactionSpec,
)
from app.services.reaction_browser_base import ReactionBrowser as BaseReactionBrowser


class ReactionBrowser(BaseReactionBrowser):
    """Adds current source UI triggers and text-only challenge detection."""

    @staticmethod
    def _has_challenge(page: Page) -> bool:
        if BaseReactionBrowser._has_challenge(page):
            return True
        body = page.locator("body").inner_text(timeout=5_000).lower()
        markers = (
            "verifiëren dat onze bezoekers echte mensen zijn",
            "verify that you are human",
            "controleer of je een mens bent",
            "checking your browser",
            "security verification",
        )
        return any(marker in body for marker in markers)

    @staticmethod
    def _follow_action(page: Page, spec: ReactionSpec) -> None:
        if ReactionBrowser._has_challenge(page):
            return
        if "#respond-form-guest" in spec.form_selectors:
            trigger = page.get_by_text(re.compile(r"^Ja, ik heb interesse$", re.I)).first
            if trigger.count():
                trigger.click()
        elif "form:has(#Message):has(#Firstname)" in spec.form_selectors:
            trigger = page.locator("a[href='#ContactMeWidget']").first
            if trigger.count():
                trigger.click()
        BaseReactionBrowser._follow_action(page, spec)
        if not spec.account_required:
            page.locator("form:has(input[type='password'])").evaluate_all(
                "forms => forms.forEach(form => form.remove())"
            )


__all__ = [
    "REACTION_SPECS",
    "BrowserReactionResult",
    "ReactionBrowser",
    "ReactionSpec",
]
