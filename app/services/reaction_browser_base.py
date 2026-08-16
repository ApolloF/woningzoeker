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
from app.services.captcha_solver import CaptchaSolver
from app.services.interactive_captcha import (
    VIEWPORT_HEIGHT,
    VIEWPORT_WIDTH,
    SessionMeta,
    solve_interactively,
)

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
        form_selectors=(
            "form.form--external_listing_contact_form",
            "form[action*='/reageer/']",
            "form:has(textarea):has(button[type='submit'])",
        ),
    ),
    "pararius": ReactionSpec(
        account_required=True,
        login_url="https://www.pararius.nl/inloggen",
        action_href_parts=("/contact/", "/reageer/"),
        form_selectors=(
            "form.form--contact-agent-huurprofiel",
            "form.form--external_listing_contact_form",
            "form[action*='/contact/']",
            "form[action*='/reageer/']",
            "form:has(textarea):has(button[type='submit'])",
        ),
    ),
    "woldring": ReactionSpec(
        account_required=True,
        login_url="https://woldringverhuur.nl/mijn-woldring/inloggen",
        form_selectors=("form:has(textarea):has(button[type='submit'])",),
    ),
    "gruno_vastgoed": ReactionSpec(
        form_selectors=("form:has(#Message):has(#Firstname)", "form:has(#Message)"),
    ),
    "123wonen_groningen": ReactionSpec(
        form_selectors=("#formulier-pandbrochure-form",),
    ),
    "maxx_groningen": ReactionSpec(
        form_selectors=("#respond-form-guest",),
    ),
    "rotsvast_groningen": ReactionSpec(
        action_href_parts=("#modal-form-appointment-object",),
        form_selectors=(
            "form#gform_7",
            "form#gform_17",
            "form[id*='gform']",
            "form:has(textarea):visible",
        ),
    ),
    "pandomo": ReactionSpec(
        action_href_parts=("#inschrijven-huur-modal",),
    ),
    "campus_groningen": ReactionSpec(
        account_required=True,
        login_url="https://www.campusgroningen.com/login",
        form_selectors=(
            "form#info-vraag-0",
            "form[id*='info-vraag']",
            "form.form-horizontal:has(textarea):visible",
            "form:has(textarea):visible",
        ),
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

    _legal_checkbox = re.compile(
        r"agree|agreement|terms|condition|privacy|accept|toestemming|akkoord|voorwaarden|"
        r"verklaring|consent|avg|waarheid|truth|correct ingevuld|juist ingevuld|data.?sharing|leeftijd|age",
        re.I,
    )
    _sensitive_checkbox = re.compile(
        r"marketing|nieuwsbrief|newsletter|commercial|reclame|betaling|payment|kosten|cost|"
        r"trial|probeer|huurprofiel|cent|abonnement|subscription|"
        r"identiteit|identity|document|upload|paspoort|passport|id.?kaart|loonstrook|payslip",
        re.I,
    )
    _sensitive_field = re.compile(
        r"income|salary|employer|birth|geboorte|bsn|passport|identity|document|bank|iban|id[_-]",
        re.I,
    )

    def __init__(
        self, settings: Settings, captcha_solver: CaptchaSolver | None = None
    ) -> None:
        self.settings = settings
        self.captcha_solver = captcha_solver or CaptchaSolver(self.settings)

    _default_user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    def _browser_launch_args(self) -> dict[str, Any]:
        args: dict[str, Any] = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-position=0,0",
                "--ignore-certificate-errors",
                "--ignore-certificate-errors-spki-list",
            ],
        }
        if self.settings.chromium_executable_path:
            args["executable_path"] = self.settings.chromium_executable_path
        return args

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
            browser = playwright.chromium.launch(**self._browser_launch_args())
            context_args: dict[str, Any] = {
                "viewport": {"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
                "user_agent": self._default_user_agent,
            }
            if credential.storage_state:
                context_args["storage_state"] = credential.storage_state
            context = browser.new_context(**context_args)
            context.set_default_timeout(self.settings.reaction_browser_timeout_seconds * 1000)
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
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
        accept_legal_confirmations: bool = False,
        listing_title: str = "",
        source_display: str = "",
    ) -> BrowserReactionResult:
        spec = REACTION_SPECS.get(source_name)
        if spec is None:
            return self._review("UNSUPPORTED_SOURCE", "Geen gecontroleerde reactieflow voor deze bron.")
        artifact_dir = Path(self.settings.reaction_artifact_dir) / str(submission_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        before_path = artifact_dir / "before-submit.png"
        after_path = artifact_dir / "after-submit.png"
        challenge_meta = SessionMeta(
            submission_id=submission_id,
            listing_title=listing_title,
            listing_url=listing_url,
            source_display=source_display or source_name,
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**self._browser_launch_args())
            context_args: dict[str, Any] = {
                "viewport": {"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
                "user_agent": self._default_user_agent,
            }
            if credential and credential.storage_state:
                context_args["storage_state"] = credential.storage_state
            context = browser.new_context(**context_args)
            context.set_default_timeout(self.settings.reaction_browser_timeout_seconds * 1000)
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            try:
                if spec.account_required and not (credential and credential.storage_state):
                    login_result = self._ensure_login(
                        page, context, spec, listing_url, credential, source_name, challenge_meta
                    )
                    if login_result is not None:
                        return login_result

                page.goto(listing_url, wait_until="domcontentloaded")
                self._dismiss_cookie_banner(page)
                if spec.account_required and page.locator("input[type='password']:visible").count():
                    login_result = self._ensure_login(
                        page, context, spec, listing_url, credential, source_name, challenge_meta
                    )
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
                if (
                    self._has_challenge(page)
                    and not self.solve_page_challenge(page)
                    and not self._human_solve_challenge(page, challenge_meta)
                ):
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
                if form is None and spec.action_href_parts:
                    self._follow_action(page, spec)
                    form = self._find_form(page, spec)

                if form is None and spec.account_required and credential and (credential.username and credential.password):
                    login_result = self._ensure_login(
                        page, context, spec, listing_url, credential, source_name, challenge_meta
                    )
                    if login_result is None:
                        page.goto(listing_url, wait_until="domcontentloaded")
                        self._dismiss_cookie_banner(page)
                        self._follow_action(page, spec)
                        form = self._find_form(page, spec)

                if form is None:
                    body_text = page.locator("body").inner_text().lower()
                    if any(
                        arch in body_text
                        for arch in (
                            "advertentie gearchiveerd",
                            "niet meer beschikbaar",
                            "woning is verhuurd",
                            "deze woning is verhuurd",
                            "verhuurd onder voorbehoud",
                            "open huis heeft inmiddels plaatsgevonden",
                            "volgeboekt",
                        )
                    ):
                        return self._with_storage(
                            context,
                            self._review("LISTING_ARCHIVED", "Deze woning is inmiddels verhuurd, volgeboekt of gearchiveerd."),
                        )
                    return self._with_storage(
                        context,
                        self._review("FORM_NOT_FOUND", "Geen veilig herkenbaar reactieformulier gevonden."),
                    )
                blocker = self._form_blocker(form, accept_legal_confirmations)
                if blocker:
                    return self._with_storage(context, self._review(*blocker))

                fill_result = self._fill_form(form, contact, message, accept_legal_confirmations)
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

                submit = form.locator(
                    "button[type='submit']:visible, input[type='submit']:visible, #button-send:visible, "
                    "a.btn:has-text('Plan'):visible, a:has-text('Plan'):visible, a:has-text('Verzenden'):visible, "
                    "a:has-text('Reageren'):visible, button:has-text('Plan'):visible, button[type='submit'], input[type='submit']"
                ).first
                if not submit.count():
                    return self._with_storage(
                        context,
                        self._review("SUBMIT_NOT_FOUND", "Geen expliciete verzendknop gevonden."),
                    )
                with contextlib.suppress(Exception):
                    submit.click(timeout=5000)
                page.wait_for_timeout(2000)
                with contextlib.suppress(TimeoutError):
                    page.wait_for_load_state("domcontentloaded", timeout=5_000)
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
        challenge_meta: SessionMeta | None = None,
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
        if (
            self._has_challenge(page)
            and not self.solve_page_challenge(page)
            and not self._human_solve_challenge(page, challenge_meta)
        ):
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
        form = password.locator("xpath=ancestor::form[1]")
        scope = form if form.count() else page
        username = scope.locator("input[type='email']:visible").first
        if not username.count():
            username = scope.locator(
                "input[name*='email' i]:visible, input[name*='user' i]:visible"
            ).first
        if not username.count():
            return self._review("LOGIN_FORM_UNKNOWN", "Het inlogformulier is veranderd.")
        username.fill(credential.username)
        password.fill(credential.password)
        submit = scope.locator(
            "button[type='submit']:visible, input[type='submit']:visible, "
            "button[data-ajax-submit]:visible"
        ).first
        if not submit.count():
            return self._review("LOGIN_FORM_UNKNOWN", "Geen veilige inlogknop gevonden.")
        submit.click()
        with contextlib.suppress(TimeoutError):
            page.wait_for_load_state("networkidle", timeout=10_000)
        with contextlib.suppress(TimeoutError):
            password.wait_for(state="hidden", timeout=10_000)
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
            clean_part = part.lstrip("#")
            link = page.locator(f"a[href*='{clean_part}']:visible").first
            if not link.count():
                link = page.locator(f"a[href*='{clean_part}']").first
            href = link.get_attribute("href") if link.count() else ""
            if href and not href.startswith("#") and f"#{clean_part}" not in href and clean_part not in href:
                page.goto(urljoin(page.url, href), wait_until="domcontentloaded")
                return
            if link.count():
                with contextlib.suppress(Exception):
                    link.click(force=True)
            with contextlib.suppress(Exception):
                page.evaluate(
                    """(targetId) => {
                        const els = document.querySelectorAll(`a[href*="${targetId}"], button[data-target*="${targetId}"], [data-bs-target*="${targetId}"]`);
                        for (const el of els) {
                            if (el.offsetParent !== null || el.offsetWidth > 0 || el.offsetHeight > 0) {
                                el.click();
                            }
                        }
                        if (els.length > 0) els[els.length - 1].click();
                        const modal = document.getElementById(targetId);
                        if (modal) {
                            modal.classList.add('show', 'in');
                            modal.style.display = 'block';
                        }
                    }""",
                    clean_part,
                )
            page.wait_for_timeout(1000)
            return
        action_btn = page.locator(
            "button:has-text('Stel een vraag'), a:has-text('Stel een vraag'), "
            "button:has-text('Reageer'), a:has-text('Reageer op deze woning'), "
            "a.button:has-text('Contact'), button:has-text('Contact'), a.listing-detail-summary__action"
        ).first
        if action_btn.count() and action_btn.is_visible():
            with contextlib.suppress(Exception):
                action_btn.click(force=True)
                page.wait_for_timeout(1000)

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
            "iframe[src*='cloudflare']",
            "iframe[src*='challenges.cloudflare.com']",
            ".cf-turnstile",
            "[name='g-recaptcha-response']",
            "[name='h-captcha-response']",
            "[name='cf-turnstile-response']",
            "[data-sitekey]",
            "img[src*='captcha' i]",
            "img[id*='captcha' i]",
        )
        return any(page.locator(selector).count() for selector in selectors)

    def _human_solve_challenge(self, page: Page, meta: SessionMeta | None) -> bool:
        """Relay the live challenge to a human and resume once they clear it.

        The person performs the CAPTCHA themselves via the dashboard; their taps and
        keystrokes are forwarded to the held page. Returns True only when the challenge
        clears (or the person confirms completion) before the timeout, so the caller may
        continue the automated flow. This is the fallback when no autonomous solver
        token is available; it never bypasses bot-detection without a human.
        """
        if meta is None or not self.settings.captcha_interactive_enabled:
            return False
        try:
            return solve_interactively(page, self.settings, meta, self._challenge_active)
        except Exception:
            logger.exception(
                "interactive captcha solve failed",
                extra={"context": {"submission_id": meta.submission_id}},
            )
            return False

    @staticmethod
    def _challenge_active(page: Page) -> bool:
        """Whether a challenge widget is active and its response token is still empty."""
        with contextlib.suppress(Exception):
            if page.locator(
                ".g-recaptcha, iframe[src*='recaptcha'], [name='g-recaptcha-response']"
            ).count():
                token = page.locator("textarea[name='g-recaptcha-response'], [name='g-recaptcha-response']").first
                if not token.count() or not (token.input_value() or "").strip():
                    return True
            if page.locator("iframe[src*='hcaptcha'], [name='h-captcha-response']").count():
                token = page.locator("textarea[name='h-captcha-response'], [name='h-captcha-response']").first
                if not token.count() or not (token.input_value() or "").strip():
                    return True
            if page.locator(
                "[data-sitekey]:not(.g-recaptcha), iframe[src*='cloudflare'], iframe[src*='challenges.cloudflare.com'], .cf-turnstile"
            ).count():
                turnstile = page.locator("input[name='cf-turnstile-response'], [name='cf-turnstile-response']").first
                if not turnstile.count() or not (turnstile.input_value() or "").strip():
                    return True
            if page.locator("img[src*='captcha' i], img[id*='captcha' i]").count():
                captcha_input = page.locator("input[name*='captcha' i], input[id*='captcha' i]").first
                if captcha_input.count() and not (captcha_input.input_value() or "").strip():
                    return True
        return False

    def solve_page_challenge(self, page: Page) -> bool:
        if not self.captcha_solver.is_enabled():
            return False

        try:
            # 1. reCAPTCHA v2 / v3
            recaptcha_frame = page.locator("iframe[src*='recaptcha']").first
            recaptcha_el = page.locator(".g-recaptcha, [data-sitekey]:not(.cf-turnstile):not(.h-captcha)").first
            sitekey = None
            is_v3 = False
            if recaptcha_el.count() and recaptcha_el.get_attribute("data-sitekey"):
                sitekey = recaptcha_el.get_attribute("data-sitekey")
                cls_name = (recaptcha_el.get_attribute("class") or "").lower()
                if "v3" in cls_name or "recaptchav3" in cls_name:
                    is_v3 = True
            elif recaptcha_frame.count():
                src = recaptcha_frame.get_attribute("src") or ""
                match = re.search(r"[?&](?:k|sitekey)=([^&]+)", src)
                if match:
                    sitekey = match.group(1)

            if sitekey:
                if is_v3:
                    token = self.captcha_solver.solve_recaptcha_v3(page.url, sitekey)
                else:
                    s_data = recaptcha_el.get_attribute("data-s") if recaptcha_el.count() else None
                    token = self.captcha_solver.solve_recaptcha_v2(page.url, sitekey, s_data=s_data)
                if token:
                    self._inject_captcha_token(page, token, field_names=["g-recaptcha-response"])
                    with contextlib.suppress(Exception):
                        page.wait_for_timeout(1000)
                    return True

            # 2. hCaptcha
            hcaptcha_frame = page.locator("iframe[src*='hcaptcha']").first
            hcaptcha_el = page.locator(".h-captcha[data-sitekey]").first
            h_sitekey = None
            if hcaptcha_el.count() and hcaptcha_el.get_attribute("data-sitekey"):
                h_sitekey = hcaptcha_el.get_attribute("data-sitekey")
            elif hcaptcha_frame.count():
                src = hcaptcha_frame.get_attribute("src") or ""
                match = re.search(r"[?&](?:sitekey|k)=([^&]+)", src)
                if match:
                    h_sitekey = match.group(1)

            if h_sitekey:
                token = self.captcha_solver.solve_hcaptcha(page.url, h_sitekey)
                if token:
                    self._inject_captcha_token(
                        page, token, field_names=["h-captcha-response", "g-recaptcha-response"]
                    )
                    with contextlib.suppress(Exception):
                        page.wait_for_timeout(1000)
                    return True

            # 3. Turnstile / Cloudflare
            turnstile_frame = page.locator(
                "iframe[src*='turnstile'], iframe[src*='challenges.cloudflare.com']"
            ).first
            turnstile_el = page.locator(".cf-turnstile[data-sitekey], [class*='turnstile'][data-sitekey]").first
            t_sitekey = None
            if turnstile_el.count() and turnstile_el.get_attribute("data-sitekey"):
                t_sitekey = turnstile_el.get_attribute("data-sitekey")
            elif turnstile_frame.count():
                src = turnstile_frame.get_attribute("src") or ""
                match = re.search(r"[?&](?:sitekey|k)=([^&]+)", src)
                if match:
                    t_sitekey = match.group(1)

            if t_sitekey:
                token = self.captcha_solver.solve_turnstile(page.url, t_sitekey)
                if token:
                    self._inject_captcha_token(
                        page, token, field_names=["cf-turnstile-response", "g-recaptcha-response"]
                    )
                    with contextlib.suppress(Exception):
                        page.wait_for_timeout(1000)
                    return True

            # 4. Image CAPTCHA
            captcha_img = page.locator("img[src*='captcha' i], img[id*='captcha' i]").first
            captcha_input = page.locator(
                "input[name*='captcha' i], input[id*='captcha' i]"
            ).first
            if captcha_img.count() and captcha_input.count() and captcha_img.is_visible():
                import base64

                img_bytes = captcha_img.screenshot(type="png")
                img_b64 = base64.b64encode(img_bytes).decode("utf-8")
                solved_text = self.captcha_solver.solve_image_captcha(img_b64)
                if solved_text:
                    captcha_input.fill(solved_text)
                    with contextlib.suppress(Exception):
                        page.wait_for_timeout(1000)
                    return True
        except Exception:
            logger.exception("Error while solving page captcha challenge")

        return False

    @staticmethod
    def _inject_captcha_token(page: Page, token: str, field_names: list[str]) -> None:
        page.evaluate(
            """({ token, fieldNames }) => {
                for (const name of fieldNames) {
                    let elems = document.querySelectorAll(`[name="${name}"], [id="${name}"]`);
                    if (!elems.length) {
                        const textarea = document.createElement("textarea");
                        textarea.name = name;
                        textarea.id = name;
                        textarea.style.display = "none";
                        document.body.appendChild(textarea);
                        elems = [textarea];
                    }
                    elems.forEach(el => {
                        el.value = token;
                        el.innerHTML = token;
                        el.dispatchEvent(new Event("input", { bubbles: true }));
                        el.dispatchEvent(new Event("change", { bubbles: true }));
                    });
                }
                document.querySelectorAll("[data-callback]").forEach(el => {
                    const fnName = el.getAttribute("data-callback");
                    if (fnName && typeof window[fnName] === "function") {
                        try { window[fnName](token); } catch (e) {}
                    }
                });
                if (window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients) {
                    for (const cid in window.___grecaptcha_cfg.clients) {
                        const client = window.___grecaptcha_cfg.clients[cid];
                        for (const k in client) {
                            if (client[k] && typeof client[k].callback === 'function') {
                                try { client[k].callback(token); } catch (e) {}
                            }
                            if (client[k] && typeof client[k] === 'object') {
                                for (const subk in client[k]) {
                                    if (client[k][subk] && typeof client[k][subk].callback === 'function') {
                                        try { client[k][subk].callback(token); } catch (e) {}
                                    }
                                }
                            }
                        }
                    }
                }
            }""",
            {"token": token, "fieldNames": field_names},
        )

    def _find_form(self, page: Page, spec: ReactionSpec) -> Locator | None:
        for selector in spec.form_selectors:
            form = page.locator(selector).first
            if form.count():
                with contextlib.suppress(Exception):
                    form.wait_for(state="visible", timeout=2000)
                if form.is_visible():
                    return form
        forms = page.locator("form")
        for index in range(forms.count()):
            form = forms.nth(index)
            if not form.is_visible():
                continue
            has_message = form.locator("textarea:visible").count() > 0
            has_email = form.locator("input[type='email']:visible, input[name*='email' i]:visible").count() > 0
            has_submit = form.locator("button[type='submit'], input[type='submit'], button[id*='submit']").count() > 0
            if (has_message or form.locator("input:visible").count() >= 2) and has_submit:
                return form
        return None

    @classmethod
    def _legal_checkbox_action(cls, descriptor: str, allow_accept: bool) -> str | None:
        if cls._sensitive_checkbox.search(descriptor):
            return "review"
        if not cls._legal_checkbox.search(descriptor):
            return None
        if not allow_accept:
            return "review"
        return "accept"

    def _form_blocker(
        self, form: Locator, accept_legal_confirmations: bool = False
    ) -> tuple[str, str] | None:
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
            with contextlib.suppress(Exception):
                label_text = checkbox.evaluate(
                    "el => el.closest('label')?.innerText || "
                    "(el.id ? document.querySelector(`label[for=\"${el.id}\"]`)?.innerText : '') || ''"
                )
                if isinstance(label_text, str):
                    descriptor = f"{descriptor} {label_text}"
            checkbox_action = self._legal_checkbox_action(descriptor, accept_legal_confirmations)
            is_required = bool(
                checkbox.get_attribute("required") is not None
                or checkbox.get_attribute("aria-required") == "true"
                or checkbox.get_attribute("data-val-booleanrequired") is not None
            )
            is_checked = checkbox.is_checked()

            if checkbox_action == "accept":
                if not checkbox.is_disabled() and not is_checked:
                    with contextlib.suppress(Exception):
                        checkbox.check(force=True)
                continue
            if checkbox_action == "review":
                if is_required or is_checked:
                    return (
                        "LEGAL_CONFIRMATION_REQUIRED",
                        "Het formulier vraagt om een voorwaarden-, privacy- of andere persoonlijke verklaring.",
                    )
                continue
        return None

    def _fill_form(
        self,
        form: Locator,
        contact: PrivateContactData,
        message: str,
        accept_legal_confirmations: bool = False,
    ) -> list[str] | BrowserReactionResult:
        has_last_field = (
            form.locator(
                "input[name*='last' i], input[name*='achternaam' i], input[id*='last' i], input[id*='achternaam' i], input[placeholder*='achternaam' i]"
            ).count()
            > 0
        )
        values = {
            "first": contact.first_name if has_last_field else f"{contact.first_name} {contact.last_name}".strip(),
            "last": contact.last_name,
            "initial": contact.initials,
            "email": str(contact.email),
            "phone": contact.phone,
            "mobile": contact.phone,
            "address": contact.address,
            "number": contact.house_number,
            "city": contact.city,
            "zip": getattr(contact, "postcode", "") or "9711HB",
            "message": message,
        }
        filled: list[str] = []

        # Handle selects (e.g. aanhef / salutation / gender)
        selects = form.locator("select")
        for index in range(selects.count()):
            sel = selects.nth(index)
            if not sel.is_visible() or sel.is_disabled():
                continue
            sel_desc = " ".join(filter(None, (sel.get_attribute("name"), sel.get_attribute("id")))).lower()
            if re.search(r"aanhef|salutation|gender|geslacht|title", sel_desc):
                for opt in sel.locator("option").all():
                    txt = opt.inner_text()
                    if re.search(r"heer|dhr|man|mr", txt, re.I):
                        with contextlib.suppress(Exception):
                            sel.select_option(value=opt.get_attribute("value"))
                            filled.append(sel_desc[:120])
                        break

        # Handle legal checkboxes when accepted
        if accept_legal_confirmations:
            checkboxes = form.locator("input[type='checkbox']")
            for index in range(checkboxes.count()):
                cb = checkboxes.nth(index)
                if not cb.is_visible() or cb.is_disabled() or cb.is_checked():
                    continue
                cb_desc = " ".join(
                    filter(
                        None,
                        (
                            cb.get_attribute("name"),
                            cb.get_attribute("id"),
                            cb.get_attribute("aria-label"),
                        ),
                    )
                ).lower()
                if self._legal_checkbox.search(cb_desc):
                    with contextlib.suppress(Exception):
                        cb.check(force=True)
                        filled.append(cb_desc[:120])

        filled_keys: set[str] = set()
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
                val_present = ""
                with contextlib.suppress(Exception):
                    val_present = (
                        control.input_value()
                        if control.evaluate("el => 'value' in el")
                        else (control.inner_text() or "")
                    ).strip()
                if val_present:
                    filled.append(descriptor[:120])
                    continue
                return self._review(
                    "SENSITIVE_FIELD_REQUIRED",
                    "Het formulier vraagt om aanvullende gevoelige gegevens.",
                )
            key = self._field_key(descriptor, control_type, control.evaluate("el => el.tagName"))
            if key is None:
                val_present = ""
                with contextlib.suppress(Exception):
                    val_present = (
                        control.input_value()
                        if control.evaluate("el => 'value' in el")
                        else (control.inner_text() or "")
                    ).strip()
                if val_present:
                    filled.append(descriptor[:120])
                    continue
                required = control.get_attribute("required") is not None or (
                    control.get_attribute("aria-required") == "true"
                )
                if required:
                    return self._review(
                        "UNKNOWN_REQUIRED_FIELD",
                        "Een verplicht veld kan niet betrouwbaar worden ingevuld.",
                    )
                continue
            val = values[key]
            max_len = control.get_attribute("maxlength") or control.get_attribute("data-val-length-max")
            if max_len and max_len.isdigit():
                limit = int(max_len)
                if len(val) > limit:
                    val = val[:limit]
            control.fill(val)
            filled.append(descriptor[:120] or key)
            filled_keys.add(key)
        if "message" not in filled_keys:
            if form.locator("textarea:visible").count() > 0:
                return self._review("MESSAGE_FIELD_MISSING", "Geen berichtveld gevonden.")
        return sorted(set(filled))

    @staticmethod
    def _field_key(descriptor: str, control_type: str, tag_name: str) -> str | None:
        if tag_name.upper() == "TEXTAREA":
            return "message"
        if control_type == "email":
            return "email"
        patterns = (
            ("email", r"email|e-mail"),
            ("last", r"last|achternaam|family-name|surname"),
            ("initial", r"initial|voorletter"),
            ("mobile", r"mobile|mobiel"),
            ("phone", r"phone|telefoon|\btel\b"),
            ("first", r"first|voornaam|given-name|\bnaam\b|\bname\b"),
            ("zip", r"postcode|postal|zip"),
            ("number", r"house.?number|huisnummer|address_number"),
            ("address", r"street|straat|adres|address"),
            ("city", r"city|plaats|woonplaats"),
            ("message", r"message|bericht|remark|toelichting|motivatie|motivation|reactie|comment"),
        )
        for key, pattern in patterns:
            if re.search(pattern, descriptor, re.I):
                return key
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
