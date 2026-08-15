from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import BrowserContext, Locator, Page, TimeoutError, sync_playwright

from app.config import Settings
from app.models import SubmissionState
from app.schemas import PrivateContactData, SourceCredentialData

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReactionSpec:
    account_required: bool = False
    login_url: str | None = None
    action_href_parts: tuple[str, ...] = ()
    form_selectors: tuple[str, ...] = ()
    review_only_code: str | None = None


REACTION_SPECS: dict[str, ReactionSpec] = {
    "funda_rentals": ReactionSpec(
        action_href_parts=("/makelaar-contact/",),
        form_selectors=("form:has(textarea):has(input[type='email'])",),
    ),
    "huurwoningen": ReactionSpec(
        account_required=True,
        login_url="https://www.huurwoningen.nl/account/inloggen/",
        action_href_parts=("/reageer/",),
        form_selectors=("form:has(textarea):has(button[type='submit'])",),
    ),
    "pararius": ReactionSpec(
        account_required=True,
        login_url="https://www.pararius.nl/inloggen",
        action_href_parts=("/contact/",),
        form_selectors=("form:has(textarea):has(button[type='submit'])",),
    ),
    "woldring": ReactionSpec(
        account_required=True,
        login_url="https://www.woldring.nl/mijn-woldring/inloggen",
        form_selectors=("form:has(textarea):has(button[type='submit'])",),
    ),
    "gruno_vastgoed": ReactionSpec(
        form_selectors=("form:has(#Message):has(#Firstname)",),
    ),
    "123wonen_groningen": ReactionSpec(
        form_selectors=("#formulier-pandbrochure-form",),
    ),
    "maxx_groningen": ReactionSpec(
        form_selectors=("#respond-form-guest",),
    ),
    "rotsvast_groningen": ReactionSpec(
        action_href_parts=("#modal-form-appointment-object",),
    ),
    "pandomo": ReactionSpec(
        action_href_parts=("#inschrijven-huur-modal",),
    ),
    "campus_groningen": ReactionSpec(
        account_required=True,
    ),
    "bulten_vastgoed": ReactionSpec(
        form_selectors=("form:has(textarea):has(button[type='submit'])",),
    ),
}


@dataclass
class BrowserReactionResult:
    state: SubmissionState
    code: str
    summary: str
    field_names: list[str] = field(default_factory=list)
    browser_result: dict[str, Any] = field(default_factory=dict)
    before_screenshot: str | None = None
    after_screenshot: str | None = None
    storage_state: dict[str, Any] | None = None


@dataclass(frozen=True)
class LoginCheckResult:
    ok: bool
    code: str
    summary: str
    storage_state: dict[str, Any] | None = None


class ReactionBrowser:
    """Conservative browser automation: fill known fields and stop on ambiguity."""

    _legal_checkbox = re.compile(r"agree|terms|condition|privacy|accept|toestemming", re.I)
    _sensitive_field = re.compile(
        r"income|salary|employer|birth|geboorte|bsn|passport|identity|document|bank|iban|id[_-]",
        re.I,
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def check_login(
        self,
        source_name: str,
        credential: SourceCredentialData | None,
    ) -> LoginCheckResult:
        """Verify an account session without opening or submitting a reaction form."""
        spec = REACTION_SPECS.get(source_name)
        if spec is None or not spec.account_required or not spec.login_url:
            return LoginCheckResult(
                False,
                "LOGIN_CHECK_UNSUPPORTED",
                "Deze bron heeft geen veilige automatische inlogcontrole.",
            )
        if credential is None:
            return LoginCheckResult(False, "CREDENTIALS_MISSING", "Inloggegevens ontbreken.")

        with sync_playwright() as playwright:
            launch_args: dict[str, Any] = {"headless": True}
            if self.settings.chromium_executable_path:
                launch_args["executable_path"] = self.settings.chromium_executable_path
            browser = playwright.chromium.launch(**launch_args)
            context_args: dict[str, Any] = {}
            if credential.storage_state:
                context_args["storage_state"] = credential.storage_state
            context = browser.new_context(**context_args)
            context.set_default_timeout(self.settings.reaction_browser_timeout_seconds * 1000)
            page = context.new_page()
            try:
                failure = self._ensure_login(
                    page,
                    context,
                    spec,
                    spec.login_url,
                    credential,
                    source_name,
                )
                if failure is not None:
                    return LoginCheckResult(
                        False,
                        failure.code,
                        failure.summary,
                        failure.storage_state,
                    )
                return LoginCheckResult(
                    True,
                    "LOGIN_OK",
                    "Inloggen en sessie gecontroleerd.",
                    dict(context.storage_state()),
                )
            except TimeoutError:
                return LoginCheckResult(
                    False,
                    "BROWSER_TIMEOUT",
                    "De inlogpagina reageerde niet binnen de tijdslimiet.",
                )
            finally:
                context.close()
                browser.close()

    def react(
        self,
        *,
        source_name: str,
        listing_url: str,
        message: str,
        contact: PrivateContactData,
        credential: SourceCredentialData | None,
        submission_id: int,
        allow_submit: bool,
    ) -> BrowserReactionResult:
        spec = REACTION_SPECS.get(source_name)
        if spec is None:
            return self._review("UNSUPPORTED_SOURCE", "Geen gecontroleerde reactieflow voor deze bron.")
        artifact_dir = Path(self.settings.reaction_artifact_dir) / str(submission_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        before_path = artifact_dir / "before-submit.png"
        after_path = artifact_dir / "after-submit.png"

        with sync_playwright() as playwright:
            launch_args: dict[str, Any] = {"headless": True}
            if self.settings.chromium_executable_path:
                launch_args["executable_path"] = self.settings.chromium_executable_path
            browser = playwright.chromium.launch(**launch_args)
            context_args: dict[str, Any] = {}
            if credential and credential.storage_state:
                context_args["storage_state"] = credential.storage_state
            context = browser.new_context(**context_args)
            context.set_default_timeout(self.settings.reaction_browser_timeout_seconds * 1000)
            page = context.new_page()
            try:
                login_result = self._ensure_login(page, context, spec, listing_url, credential, source_name)
                if login_result is not None:
                    return login_result

                page.goto(listing_url, wait_until="domcontentloaded")
                self._dismiss_cookie_banner(page)
                if spec.review_only_code:
                    return self._with_storage(
                        context,
                        self._review(
                            spec.review_only_code,
                            "Deze bron vereist een nog niet veilig verifieerbare vervolgstap.",
                        ),
                    )
                self._follow_action(page, spec)
                self._dismiss_cookie_banner(page)
                if self._has_challenge(page):
                    return self._with_storage(
                        context,
                        self._review(
                            "CAPTCHA_REQUIRED",
                            "De site vraagt om een menselijke CAPTCHA-controle.",
                        ),
                    )
                if page.locator("input[type='password']").count():
                    return self._with_storage(
                        context,
                        self._review("LOGIN_REQUIRED", "De sitesessie is niet ingelogd."),
                    )

                form = self._find_form(page, spec)
                if form is None:
                    return self._with_storage(
                        context,
                        self._review("FORM_NOT_FOUND", "Geen veilig herkenbaar reactieformulier gevonden."),
                    )
                blocker = self._form_blocker(form)
                if blocker:
                    return self._with_storage(context, self._review(*blocker))

                fill_result = self._fill_form(form, contact, message)
                if isinstance(fill_result, BrowserReactionResult):
                    return self._with_storage(context, fill_result)
                field_names = fill_result
                page.screenshot(path=str(before_path), full_page=True)
                if not allow_submit:
                    return self._with_storage(
                        context,
                        BrowserReactionResult(
                            state=SubmissionState.DRY_RUN_STOPPED,
                            code="DRY_RUN_STOPPED",
                            summary="Formulier ingevuld; dry-run stopte voor verzenden.",
                            field_names=field_names,
                            before_screenshot=str(before_path),
                            browser_result={"url": page.url, "submitted": False},
                        ),
                    )

                submit = form.locator("button[type='submit'], input[type='submit']").last
                if not submit.count():
                    return self._with_storage(
                        context,
                        self._review("SUBMIT_NOT_FOUND", "Geen expliciete verzendknop gevonden."),
                    )
                submit.click()
                with contextlib.suppress(TimeoutError):
                    page.wait_for_load_state("networkidle", timeout=10_000)
                page.screenshot(path=str(after_path), full_page=True)
                confirmation = self._confirmation_observed(page)
                return self._with_storage(
                    context,
                    BrowserReactionResult(
                        state=SubmissionState.SENT,
                        code="SUBMIT_COMPLETED",
                        summary=(
                            "Reactie verzonden en bevestiging gezien."
                            if confirmation
                            else "Verzendactie uitgevoerd; geen eenduidige bevestiging gevonden."
                        ),
                        field_names=field_names,
                        browser_result={
                            "url": page.url,
                            "submitted": True,
                            "confirmation_observed": confirmation,
                        },
                        before_screenshot=str(before_path),
                        after_screenshot=str(after_path),
                    ),
                )
            except TimeoutError:
                logger.warning("reaction browser timed out", extra={"context": {"source": source_name}})
                return self._with_storage(
                    context,
                    self._review("BROWSER_TIMEOUT", "De site reageerde niet binnen de tijdslimiet."),
                )
            finally:
                context.close()
                browser.close()

    def _ensure_login(
        self,
        page: Page,
        context: BrowserContext,
        spec: ReactionSpec,
        listing_url: str,
        credential: SourceCredentialData | None,
        source_name: str,
    ) -> BrowserReactionResult | None:
        if not spec.account_required:
            return None
        if credential is None:
            return self._review("CREDENTIALS_MISSING", "Voor deze bron ontbreken inloggegevens.")
        login_url = spec.login_url or listing_url
        page.goto(login_url, wait_until="domcontentloaded")
        self._dismiss_cookie_banner(page)
        password = page.locator("input[type='password']:visible").first
        if not password.count():
            return None
        if self._has_auth_challenge(page):
            return self._with_storage(
                context,
                self._review("REAUTHENTICATION_REQUIRED", "De site vraagt om extra inlogverificatie."),
            )
        if self._has_challenge(page):
            return self._with_storage(
                context,
                self._review("CAPTCHA_REQUIRED", "Inloggen vereist een menselijke CAPTCHA-controle."),
            )
        if not credential.username or not credential.password:
            return self._with_storage(
                context,
                self._review(
                    "REAUTHENTICATION_REQUIRED",
                    "De beveiligde browsersessie is verlopen; log opnieuw in via de sessie-instelling.",
                ),
            )
        username = page.locator("input[type='email']:visible").first
        if not username.count():
            username = page.locator("input[name*='email' i]:visible, input[name*='user' i]:visible").first
        if not username.count():
            return self._review("LOGIN_FORM_UNKNOWN", "Het inlogformulier is veranderd.")
        username.fill(credential.username)
        password.fill(credential.password)
        submit = page.locator("button[type='submit']:visible, input[type='submit']:visible").first
        if not submit.count():
            return self._review("LOGIN_FORM_UNKNOWN", "Geen veilige inlogknop gevonden.")
        submit.click()
        with contextlib.suppress(TimeoutError):
            page.wait_for_load_state("networkidle", timeout=10_000)
        if self._has_auth_challenge(page):
            return self._with_storage(
                context,
                self._review("REAUTHENTICATION_REQUIRED", "De site vraagt om extra inlogverificatie."),
            )
        if page.locator("input[type='password']:visible").count():
            return self._with_storage(
                context,
                self._review("LOGIN_FAILED", f"Inloggen bij {source_name} is niet gelukt."),
            )
        return None

    @staticmethod
    def _dismiss_cookie_banner(page: Page) -> None:
        patterns = re.compile(r"alleen noodzakelijk|weigeren|reject|necessary only", re.I)
        try:
            button = page.get_by_role("button", name=patterns).first
            if button.count() and button.is_visible():
                button.click(timeout=1_000)
        except TimeoutError:
            return

    @staticmethod
    def _follow_action(page: Page, spec: ReactionSpec) -> None:
        for part in spec.action_href_parts:
            link = page.locator(f"a[href*='{part}']").first
            if not link.count():
                continue
            href = link.get_attribute("href") or ""
            if href.startswith("#"):
                link.click()
            else:
                page.goto(urljoin(page.url, href), wait_until="domcontentloaded")
            return

    @staticmethod
    def _has_auth_challenge(page: Page) -> bool:
        text = page.locator("body").inner_text().lower()
        return bool(
            page.locator(
                "input[autocomplete='one-time-code'], input[name*='otp' i], input[name*='code' i]"
            ).count()
            or any(
                marker in text for marker in ("two-factor", "twee-factor", "verificatiecode", "authenticator")
            )
        )

    @staticmethod
    def _has_challenge(page: Page) -> bool:
        selectors = (
            ".g-recaptcha",
            "iframe[src*='recaptcha']",
            "iframe[src*='hcaptcha']",
            "[name='g-recaptcha-response']",
            "[data-sitekey]",
        )
        return any(page.locator(selector).count() for selector in selectors)

    def _find_form(self, page: Page, spec: ReactionSpec) -> Locator | None:
        for selector in spec.form_selectors:
            form = page.locator(selector).first
            if form.count() and form.is_visible():
                return form
        forms = page.locator("form")
        for index in range(forms.count()):
            form = forms.nth(index)
            if not form.is_visible():
                continue
            has_message = form.locator("textarea:visible").count() > 0
            has_email = form.locator("input[type='email']:visible").count() > 0
            has_submit = form.locator("button[type='submit'], input[type='submit']").count() > 0
            if has_message and has_email and has_submit:
                return form
        return None

    def _form_blocker(self, form: Locator) -> tuple[str, str] | None:
        action = form.get_attribute("action") or ""
        if "/shop/add" in action:
            return "REGISTRATION_OR_PURCHASE_FLOW", "Dit formulier start registratie of aankoop."
        if form.locator("input[type='file']").count():
            return "DOCUMENT_UPLOAD_REQUIRED", "Het formulier vraagt om documentuploads."
        checkboxes = form.locator("input[type='checkbox']")
        for index in range(checkboxes.count()):
            checkbox = checkboxes.nth(index)
            descriptor = " ".join(
                filter(
                    None,
                    (
                        checkbox.get_attribute("name"),
                        checkbox.get_attribute("id"),
                        checkbox.get_attribute("aria-label"),
                    ),
                )
            )
            if self._legal_checkbox.search(descriptor):
                return (
                    "LEGAL_CONFIRMATION_REQUIRED",
                    "Het formulier vraagt om een persoonlijke voorwaarden- of privacyverklaring.",
                )
        return None

    def _fill_form(
        self, form: Locator, contact: PrivateContactData, message: str
    ) -> list[str] | BrowserReactionResult:
        values = {
            "first": contact.first_name,
            "last": contact.last_name,
            "initial": contact.initials,
            "email": str(contact.email),
            "phone": contact.phone,
            "mobile": contact.phone,
            "address": contact.address,
            "number": contact.house_number,
            "city": contact.city,
            "message": message,
        }
        filled: list[str] = []
        controls = form.locator("input, textarea")
        for index in range(controls.count()):
            control = controls.nth(index)
            control_type = (control.get_attribute("type") or "text").lower()
            if control_type in {"hidden", "submit", "button", "checkbox", "radio", "file"}:
                continue
            if not control.is_visible() or control.is_disabled():
                continue
            descriptor = " ".join(
                filter(
                    None,
                    (
                        control.get_attribute("name"),
                        control.get_attribute("id"),
                        control.get_attribute("placeholder"),
                        control.get_attribute("autocomplete"),
                        control.get_attribute("aria-label"),
                    ),
                )
            ).lower()
            if self._sensitive_field.search(descriptor):
                return self._review(
                    "SENSITIVE_FIELD_REQUIRED",
                    "Het formulier vraagt om aanvullende gevoelige gegevens.",
                )
            key = self._field_key(descriptor, control_type, control.evaluate("el => el.tagName"))
            if key is None:
                required = control.get_attribute("required") is not None or (
                    control.get_attribute("aria-required") == "true"
                )
                if required:
                    return self._review(
                        "UNKNOWN_REQUIRED_FIELD",
                        "Een verplicht veld kan niet betrouwbaar worden ingevuld.",
                    )
                continue
            control.fill(values[key])
            filled.append(descriptor[:120] or key)
        if "message" not in {self._field_key(name, "text", "") for name in filled}:  # noqa: SIM102
            if form.locator("textarea:visible").count() == 0:
                return self._review("MESSAGE_FIELD_MISSING", "Geen berichtveld gevonden.")
        return sorted(set(filled))

    @staticmethod
    def _field_key(descriptor: str, control_type: str, tag_name: str) -> str | None:
        patterns = (
            ("email", r"email|e-mail"),
            ("first", r"first|voornaam|given-name"),
            ("last", r"last|achternaam|family-name|surname"),
            ("initial", r"initial|voorletter"),
            ("mobile", r"mobile|mobiel"),
            ("phone", r"phone|telefoon|tel\b"),
            ("number", r"house.?number|huisnummer|address_number"),
            ("address", r"street|straat|adres|address"),
            ("city", r"city|plaats|woonplaats"),
            ("message", r"message|bericht|remark|toelichting|motivatie|comment"),
        )
        if control_type == "email":
            return "email"
        if tag_name.upper() == "TEXTAREA" and not descriptor:
            return "message"
        for key, pattern in patterns:
            if re.search(pattern, descriptor, re.I):
                return key
        if tag_name.upper() == "TEXTAREA":
            return "message"
        return None

    @staticmethod
    def _confirmation_observed(page: Page) -> bool:
        body = page.locator("body").inner_text(timeout=5_000).lower()
        markers = (
            "bedankt",
            "thank you",
            "succesvol verzonden",
            "aanvraag is verzonden",
            "reactie is verstuurd",
        )
        return any(marker in body for marker in markers)

    @staticmethod
    def _review(code: str, summary: str) -> BrowserReactionResult:
        return BrowserReactionResult(
            state=SubmissionState.REVIEW_REQUIRED,
            code=code,
            summary=summary,
        )

    @staticmethod
    def _with_storage(context: BrowserContext, result: BrowserReactionResult) -> BrowserReactionResult:
        result.storage_state = dict(context.storage_state())
        return result
