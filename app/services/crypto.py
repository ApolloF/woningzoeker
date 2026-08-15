from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class CredentialCipher:
    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("master encryption key is not configured")
        self._fernet = Fernet(key.encode())

    def encrypt(self, payload: dict[str, Any]) -> str:
        serialized = json.dumps(payload, separators=(",", ":")).encode()
        return self._fernet.encrypt(serialized).decode()

    def decrypt(self, token: str) -> dict[str, Any]:
        try:
            value = json.loads(self._fernet.decrypt(token.encode()).decode())
        except (InvalidToken, json.JSONDecodeError) as exc:
            raise ValueError("credential cannot be decrypted") from exc
        if not isinstance(value, dict):
            raise ValueError("credential payload is not an object")
        return value
