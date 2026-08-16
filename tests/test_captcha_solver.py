from __future__ import annotations

from typing import Any
import pytest
from app.config import Settings
from app.services.captcha_solver import CaptchaSolver


class FakeResponse:
    def __init__(self, json_data: dict[str, Any], status_code: int = 200) -> None:
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._json_data


def test_captcha_solver_disabled_by_default() -> None:
    settings = Settings(_env_file=None)
    solver = CaptchaSolver(settings)
    assert not solver.is_enabled()
    assert solver.solve_recaptcha_v2("https://example.com", "sitekey") is None
    assert solver.solve_hcaptcha("https://example.com", "sitekey") is None
    assert solver.solve_turnstile("https://example.com", "sitekey") is None
    assert solver.solve_image_captcha("base64") is None


def test_settings_validation_for_missing_keys() -> None:
    with pytest.raises(ValueError, match="CAPSOLVER_API_KEY is required"):
        Settings(_env_file=None, captcha_solver_provider="capsolver")

    with pytest.raises(ValueError, match="ANTI_CAPTCHA_API_KEY is required"):
        Settings(_env_file=None, captcha_solver_provider="anti_captcha")


def test_capsolver_recaptcha_v2_success(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None,
        captcha_solver_provider="capsolver",
        capsolver_api_key="capsolver_secret",
    )
    solver = CaptchaSolver(settings)
    assert solver.is_enabled()

    calls: list[dict[str, Any]] = []

    class FakeHttpxClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> FakeHttpxClient:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def post(self, url: str, json: dict[str, Any]) -> FakeResponse:
            calls.append({"url": url, "json": json})
            if "createTask" in url:
                assert json["clientKey"] == "capsolver_secret"
                assert json["task"]["type"] == "ReCaptchaV2TaskProxyLess"
                assert json["task"]["websiteKey"] == "test_sitekey"
                return FakeResponse({"errorId": 0, "taskId": "cap_task_123"})
            else:
                assert json["clientKey"] == "capsolver_secret"
                assert json["taskId"] == "cap_task_123"
                return FakeResponse(
                    {"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "solved_token_xyz"}}
                )

    monkeypatch.setattr("httpx.Client", FakeHttpxClient)

    token = solver.solve_recaptcha_v2("https://example.test", "test_sitekey")
    assert token == "solved_token_xyz"
    assert len(calls) == 2


def test_anti_captcha_recaptcha_v2_success(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None,
        captcha_solver_provider="anti_captcha",
        anti_captcha_api_key="anticaptcha_secret",
    )
    solver = CaptchaSolver(settings)
    assert solver.is_enabled()

    class FakeHttpxClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> FakeHttpxClient:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def post(self, url: str, json: dict[str, Any]) -> FakeResponse:
            if "createTask" in url:
                assert json["clientKey"] == "anticaptcha_secret"
                assert json["task"]["type"] == "RecaptchaV2TaskProxyless"
                return FakeResponse({"errorId": 0, "taskId": 98765})
            else:
                assert json["clientKey"] == "anticaptcha_secret"
                assert json["taskId"] == 98765
                return FakeResponse(
                    {"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "anti_token_abc"}}
                )

    monkeypatch.setattr("httpx.Client", FakeHttpxClient)

    token = solver.solve_recaptcha_v2("https://example.test", "test_sitekey")
    assert token == "anti_token_abc"


def test_capsolver_handles_create_task_error(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None,
        captcha_solver_provider="capsolver",
        capsolver_api_key="invalid_key",
    )
    solver = CaptchaSolver(settings)

    class FakeHttpxClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> FakeHttpxClient:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def post(self, url: str, json: dict[str, Any]) -> FakeResponse:
            return FakeResponse({"errorId": 1, "errorDescription": "ERROR_KEY_DOES_NOT_EXIST"})

    monkeypatch.setattr("httpx.Client", FakeHttpxClient)

    token = solver.solve_hcaptcha("https://example.test", "sitekey")
    assert token is None
