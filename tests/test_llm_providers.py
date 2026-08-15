from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings
from app.services.llm import AnthropicMessagesProvider, OpenAIResponsesProvider
from tests.test_llm import listing, profile


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeClient:
    response_payload: dict[str, Any] = {}
    captured_url = ""
    captured_headers: dict[str, str] = {}
    captured_body: dict[str, Any] = {}

    def __init__(self, **_: Any) -> None:
        pass

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> FakeResponse:
        type(self).captured_url = url
        type(self).captured_headers = headers
        type(self).captured_body = json
        return FakeResponse(type(self).response_payload)


VALID_JSON = (
    '{"suitable_for_two":true,"unusual_requirements":[],"needs_review":false,'
    '"explanation":"Geen blokkade.","response_draft_nl":"Beste verhuurder, interesse."}'
)


@pytest.mark.parametrize("provider_name", ["openai", "anthropic"])
def test_provider_contracts_use_structured_output(
    monkeypatch: pytest.MonkeyPatch, provider_name: str
) -> None:
    monkeypatch.setattr("app.services.llm.httpx.Client", FakeClient)
    if provider_name == "openai":
        settings = Settings(_env_file=None, llm_provider="openai", openai_api_key="test-key")
        provider = OpenAIResponsesProvider(settings)
        FakeClient.response_payload = {"output": [{"content": [{"type": "output_text", "text": VALID_JSON}]}]}
    else:
        settings = Settings(_env_file=None, llm_provider="anthropic", anthropic_api_key="test-key")
        provider = AnthropicMessagesProvider(settings)
        FakeClient.response_payload = {"content": [{"type": "text", "text": VALID_JSON}]}

    result = provider.analyze(listing(), profile(), "Veilig concept", "test-model")

    assert result.suitable_for_two is True
    assert result.response_draft_nl == "Beste verhuurder, interesse."
    if provider_name == "openai":
        assert FakeClient.captured_url.endswith("/responses")
        assert FakeClient.captured_body["store"] is False
        output_format = FakeClient.captured_body["text"]["format"]
        assert output_format["type"] == "json_schema"
        assert output_format["strict"] is True
    else:
        assert FakeClient.captured_url.endswith("/messages")
        output_format = FakeClient.captured_body["output_config"]["format"]
        assert output_format["type"] == "json_schema"
