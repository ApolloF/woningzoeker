from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pwdlib import PasswordHash

from app.config import Settings

SESSION_COOKIE = "woningzoeker_session"
LOGIN_CSRF_COOKIE = "woningzoeker_login_csrf"


@dataclass(frozen=True)
class AuthSession:
    username: str
    csrf_token: str


class AuthManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.serializer = URLSafeTimedSerializer(settings.session_secret_key or "unsafe-development-key")
        self.password_hash = PasswordHash.recommended()

    def verify_password(self, password: str) -> bool:
        if not self.settings.admin_password_hash:
            return False
        try:
            return self.password_hash.verify(password, self.settings.admin_password_hash)
        except Exception:
            return False

    def create_session(self) -> tuple[str, AuthSession]:
        session = AuthSession(username="admin", csrf_token=secrets.token_urlsafe(32))
        token = self.serializer.dumps({"sub": session.username, "csrf": session.csrf_token})
        return token, session

    def load_session(self, request: Request) -> AuthSession | None:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        try:
            data = self.serializer.loads(token, max_age=60 * 60 * 24 * 7)
        except (BadSignature, SignatureExpired):
            return None
        if data.get("sub") != "admin" or not data.get("csrf"):
            return None
        return AuthSession(username="admin", csrf_token=str(data["csrf"]))

    def require_session(self, request: Request) -> AuthSession:
        session = self.load_session(request)
        if not session:
            raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
        return session

    def verify_csrf(self, request: Request, form_token: str) -> AuthSession:
        session = self.require_session(request)
        if not secrets.compare_digest(session.csrf_token, form_token):
            raise HTTPException(status_code=403, detail="invalid CSRF token")
        return session

    def create_login_csrf(self) -> str:
        value = secrets.token_urlsafe(32)
        return self.serializer.dumps({"login_csrf": value})

    def verify_login_csrf(self, request: Request, form_token: str) -> bool:
        cookie = request.cookies.get(LOGIN_CSRF_COOKIE)
        if not cookie or not secrets.compare_digest(cookie, form_token):
            return False
        try:
            data = self.serializer.loads(cookie, max_age=600)
        except (BadSignature, SignatureExpired):
            return False
        return bool(data.get("login_csrf"))
