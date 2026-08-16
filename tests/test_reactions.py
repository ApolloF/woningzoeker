from __future__ import annotations

from decimal import Decimal
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.services.reactions as reactions_module
from app.config import Settings
from app.db import Base
from app.models import (
    CanonicalProperty,
    Credential,
    Decision,
    Listing,
    PrivateContact,
    SearchConfig,
    SourceConfig,
    SourceMode,
    Submission,
    SubmissionState,
)
from app.schemas import Criteria, PrivateContactData, SourceCredentialData
from app.services.crypto import CredentialCipher
from app.services.reaction_browser import (
    REACTION_SPECS,
    BrowserReactionResult,
    LoginCheckResult,
    ReactionBrowser,
)
from app.services.reactions import ReactionService


class FakeBrowser:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def react(self, **kwargs: Any) -> BrowserReactionResult:
        self.calls.append(kwargs)
        return BrowserReactionResult(
            state=SubmissionState.SENT,
            code="SUBMIT_COMPLETED",
            summary="sent",
            field_names=["email", "message"],
            browser_result={"submitted": True},
        )

    def check_login(self, source_name: str, credential: Any) -> LoginCheckResult:
        return LoginCheckResult(
            True,
            "LOGIN_OK",
            f"{source_name} login werkt",
            credential.storage_state,
        )


class FakeLoginLocator:
    def __init__(self, page: FakeLoginPage, kind: str) -> None:
        self.page = page
        self.kind = kind

    @property
    def first(self) -> FakeLoginLocator:
        return self

    def count(self) -> int:
        if self.kind == "password":
            return 0 if self.page.logged_in else 1
        return 1

    def locator(self, selector: str) -> FakeLoginLocator:
        if selector == "xpath=ancestor::form[1]":
            return FakeLoginLocator(self.page, "form")
        if "type='email'" in selector or "name*='email'" in selector:
            return FakeLoginLocator(self.page, "username")
        if "button[data-ajax-submit]" in selector:
            self.page.submit_selector = selector
            return FakeLoginLocator(self.page, "submit")
        raise AssertionError(f"unexpected selector: {selector}")

    def fill(self, value: str) -> None:
        if self.kind == "username":
            self.page.username = value
        elif self.kind == "password":
            self.page.password = value
        else:
            raise AssertionError(f"cannot fill {self.kind}")

    def click(self) -> None:
        assert self.kind == "submit"
        self.page.logged_in = True

    def wait_for(self, *, state: str, timeout: int) -> None:
        assert state == "hidden"
        assert timeout == 10_000
        assert self.page.logged_in


class FakeLoginPage:
    def __init__(self) -> None:
        self.logged_in = False
        self.goto_url: str | None = None
        self.username: str | None = None
        self.password: str | None = None
        self.submit_selector: str | None = None

    def goto(self, url: str, *, wait_until: str) -> None:
        self.goto_url = url
        assert wait_until == "domcontentloaded"

    def locator(self, selector: str) -> FakeLoginLocator:
        assert selector == "input[type='password']:visible"
        return FakeLoginLocator(self, "password")

    def wait_for_load_state(self, state: str, *, timeout: int) -> None:
        assert state == "networkidle"
        assert timeout == 10_000


class FakeLoginContext:
    pass


def test_supported_source_specs_cover_all_adapters() -> None:
    assert set(REACTION_SPECS) == {
        "123wonen_groningen",
        "bulten_vastgoed",
        "campus_groningen",
        "funda_rentals",
        "gruno_vastgoed",
        "huurwoningen",
        "kamernet",
        "maxx_groningen",
        "pandomo",
        "pararius",
        "rotsvast_groningen",
        "woldring",
    }


def test_field_mapping_is_allowlisted() -> None:
    assert ReactionBrowser._field_key("user_email", "email", "INPUT") == "email"
    assert ReactionBrowser._field_key("c_lastname", "text", "INPUT") == "last"
    assert ReactionBrowser._field_key("remark", "text", "TEXTAREA") == "message"
    assert ReactionBrowser._field_key("annual_income", "text", "INPUT") is None


def test_only_ordinary_legal_confirmations_can_be_auto_accepted() -> None:
    assert ReactionBrowser._legal_checkbox_action("Akkoord met privacy en voorwaarden", True) == "accept"
    assert ReactionBrowser._legal_checkbox_action("Akkoord met privacy en voorwaarden", False) == "review"
    assert ReactionBrowser._legal_checkbox_action("Marketing nieuwsbrief toestemming", True) == "review"
    assert ReactionBrowser._legal_checkbox_action("Ik verklaar mijn identiteit correct", True) == "review"


def test_campus_login_renews_expired_session_with_current_ajax_form(monkeypatch: Any) -> None:
    settings = Settings(_env_file=None)
    browser = ReactionBrowser(settings)
    page = FakeLoginPage()
    monkeypatch.setattr(browser, "_dismiss_cookie_banner", lambda _page: None)
    monkeypatch.setattr(browser, "_has_auth_challenge", lambda _page: False)
    monkeypatch.setattr(browser, "_has_challenge", lambda _page: False)

    result = browser._ensure_login(
        page,  # type: ignore[arg-type]
        FakeLoginContext(),  # type: ignore[arg-type]
        REACTION_SPECS["campus_groningen"],
        "https://www.campusgroningen.com/woning/test-1",
        SourceCredentialData(username="user@example.test", password="secret"),
        "campus_groningen",
    )

    assert result is None
    assert page.goto_url == "https://www.campusgroningen.com/login"
    assert page.username == "user@example.test"
    assert page.password == "secret"
    assert page.submit_selector is not None
    assert "button[data-ajax-submit]" in page.submit_selector


def test_sent_submission_blocks_duplicate_canonical_reaction(monkeypatch: Any) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    testing_session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(reactions_module, "SessionLocal", testing_session)

    key = Fernet.generate_key().decode()
    settings = Settings(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        master_encryption_key=key,
        auto_react_enabled=True,
        dry_run=False,
        llm_provider="openai",
        openai_api_key="test",
    )
    contact = PrivateContactData(
        first_name="Florian",
        last_name="Tester",
        initials="F.",
        email="florian@example.test",
        phone="0612345678",
        address="Teststraat",
        house_number="1",
        city="Groningen",
    )
    with testing_session() as db:
        source = SourceConfig(
            name="funda_rentals",
            display_name="Funda",
            base_url="https://example.test",
            mode=SourceMode.AUTO_REACT.value,
        )
        canonical = CanonicalProperty(
            dedup_key="x" * 64,
            normalized_address="teststraat 1",
            city="Groningen",
            rent_total=Decimal("1200"),
        )
        db.add_all([source, canonical])
        db.flush()
        listing = Listing(
            source_id=source.id,
            canonical_property_id=canonical.id,
            external_id="one",
            url="https://example.test/one",
            title="Testwoning",
            address="Teststraat 1",
            city="Groningen",
            decision=Decision.AUTO_REACT.value,
            response_draft="Ik heb interesse.",
            raw_data={"_llm": {"needs_review": False, "error": None}},
        )
        duplicate = Listing(
            source_id=source.id,
            canonical_property_id=canonical.id,
            external_id="two",
            url="https://example.test/two",
            title="Dezelfde woning",
            address="Teststraat 1",
            city="Groningen",
            decision=Decision.AUTO_REACT.value,
            response_draft="Nog een reactie.",
            raw_data={"_llm": {"needs_review": False, "error": None}},
        )
        encrypted = CredentialCipher(key).encrypt(contact.model_dump(mode="json"))
        db.add_all(
            [
                listing,
                duplicate,
                PrivateContact(id=1, encrypted_payload=encrypted),
                SearchConfig(
                    id=1,
                    config=Criteria(auto_accept_legal_confirmations=True).model_dump(mode="json"),
                ),
            ]
        )
        db.commit()
        listing_id = listing.id
        duplicate_id = duplicate.id

    fake = FakeBrowser()
    service = ReactionService(settings, browser=fake)  # type: ignore[arg-type]
    first = service.dispatch(listing_id)
    second = service.dispatch(duplicate_id)

    assert first.status == "sent"
    assert second.code == "ALREADY_SENT"
    assert len(fake.calls) == 1
    assert fake.calls[0]["accept_legal_confirmations"] is True
    with testing_session() as db:
        submissions = db.scalars(select(Submission)).all()
        assert len(submissions) == 1
        assert submissions[0].submitted_fields == {"field_names": ["email", "message"]}
        assert "florian@example.test" not in str(submissions[0].submitted_fields)


def test_login_check_persists_verified_status(monkeypatch: Any) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    testing_session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(reactions_module, "SessionLocal", testing_session)
    key = Fernet.generate_key().decode()
    settings = Settings(_env_file=None, master_encryption_key=key)
    with testing_session() as db:
        source = SourceConfig(
            name="woldring",
            display_name="Woldring",
            base_url="https://example.test",
        )
        db.add(source)
        db.flush()
        encrypted = CredentialCipher(key).encrypt(
            {"username": "user@example.test", "password": "secret", "storage_state": None}
        )
        db.add(Credential(source_id=source.id, label="default", encrypted_payload=encrypted))
        db.commit()

    service = ReactionService(settings, browser=FakeBrowser())  # type: ignore[arg-type]
    result = service.verify_credential("woldring")
    assert result["ok"] is True
    with testing_session() as db:
        credential = db.scalar(select(Credential))
        assert credential is not None
        assert credential.last_verified_at is not None
        assert credential.last_error is None


class FakeCaptchaSolver:
    def __init__(self, token: str | None = None) -> None:
        self.token = token
        self.solved_calls: list[dict[str, Any]] = []

    def is_enabled(self) -> bool:
        return self.token is not None

    def solve_recaptcha_v2(self, url: str, sitekey: str, s_data: str | None = None) -> str | None:
        self.solved_calls.append({"type": "v2", "url": url, "sitekey": sitekey, "s_data": s_data})
        return self.token

    def solve_recaptcha_v3(self, url: str, sitekey: str, page_action: str | None = None) -> str | None:
        self.solved_calls.append({"type": "v3", "url": url, "sitekey": sitekey})
        return self.token

    def solve_hcaptcha(self, url: str, sitekey: str) -> str | None:
        self.solved_calls.append({"type": "hcaptcha", "url": url, "sitekey": sitekey})
        return self.token

    def solve_turnstile(self, url: str, sitekey: str) -> str | None:
        self.solved_calls.append({"type": "turnstile", "url": url, "sitekey": sitekey})
        return self.token

    def solve_image_captcha(self, image_base64: str) -> str | None:
        self.solved_calls.append({"type": "image", "b64": image_base64})
        return self.token


class FakeElement:
    def __init__(self, count: int = 1, attrs: dict[str, str] | None = None) -> None:
        self._count = count
        self._attrs = attrs or {}

    @property
    def first(self) -> FakeElement:
        return self

    def count(self) -> int:
        return self._count

    def get_attribute(self, name: str) -> str | None:
        return self._attrs.get(name)


class FakeChallengePage:
    def __init__(self, has_challenge_after_solve: bool = False) -> None:
        self.url = "https://example.test/form"
        self.has_challenge_after_solve = has_challenge_after_solve
        self.evaluations: list[Any] = []
        self.challenge_checks = 0

    def locator(self, selector: str) -> FakeElement:
        if "recaptcha" in selector:
            return FakeElement(1, {"data-sitekey": "sitekey_12345"})
        return FakeElement(0)

    def evaluate(self, script: str, arg: Any) -> None:
        self.evaluations.append({"script": script, "arg": arg})

    def wait_for_timeout(self, ms: int) -> None:
        pass


def test_reaction_browser_solves_captcha_when_enabled() -> None:
    settings = Settings(_env_file=None)
    fake_solver = FakeCaptchaSolver(token="mocked_captcha_token")
    browser = ReactionBrowser(settings, captcha_solver=fake_solver)  # type: ignore[arg-type]

    page = FakeChallengePage(has_challenge_after_solve=False)
    browser._has_challenge = lambda _p: False  # type: ignore[assignment]

    solved = browser.solve_page_challenge(page)  # type: ignore[arg-type]

    assert solved is True
    assert len(fake_solver.solved_calls) == 1
    assert fake_solver.solved_calls[0]["sitekey"] == "sitekey_12345"
    assert len(page.evaluations) == 1
    assert page.evaluations[0]["arg"]["token"] == "mocked_captcha_token"


def test_reaction_browser_fails_when_captcha_solver_disabled() -> None:
    settings = Settings(_env_file=None)
    fake_solver = FakeCaptchaSolver(token=None)
    browser = ReactionBrowser(settings, captcha_solver=fake_solver)  # type: ignore[arg-type]

    page = FakeChallengePage()
    solved = browser.solve_page_challenge(page)  # type: ignore[arg-type]

    assert solved is False
    assert len(fake_solver.solved_calls) == 0

