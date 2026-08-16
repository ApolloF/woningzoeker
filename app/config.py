from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://woningzoeker:woningzoeker@db/woningzoeker"
    admin_password_hash: str = ""
    session_secret_key: str = ""
    master_encryption_key: str = ""
    dashboard_cookie_secure: bool = False
    public_base_url: str = "http://localhost:8000"
    dry_run: bool = True
    auto_react_enabled: bool = False
    scheduler_enabled: bool = True
    log_level: str = "INFO"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_allowed_user_ids: list[int] = Field(default_factory=list)

    chromium_executable_path: str = ""
    browser_timeout_seconds: int = Field(default=40, ge=10, le=120)
    reaction_browser_timeout_seconds: int = Field(default=45, ge=10, le=180)
    reaction_artifact_dir: str = "/app/artifacts/submissions"
    reaction_max_attempts: int = Field(default=3, ge=1, le=5)

    # Human-in-the-loop CAPTCHA solving. When enabled, a detected CAPTCHA during an
    # auto-reaction keeps the browser session alive and relays the live page to the
    # dashboard so a human can solve only the challenge; automation then resumes and
    # finishes the reaction. Never used to bypass bot-detection autonomously.
    captcha_interactive_enabled: bool = False
    captcha_solve_timeout_seconds: int = Field(default=180, ge=30, le=600)

    llm_provider: str = "disabled"
    llm_cheap_model: str = ""
    llm_standard_model: str = ""
    llm_escalation_model: str = ""
    llm_timeout_seconds: int = Field(default=45, ge=5, le=180)
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com/v1"

    captcha_solver_provider: str = "disabled"
    capsolver_api_key: str = ""
    anti_captcha_api_key: str = ""
    captcha_solver_timeout_seconds: int = Field(default=120, ge=10, le=300)

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def parse_user_ids(cls, value: object) -> object:
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            cleaned = value.strip().strip("[]()")
            return [int(item.strip()) for item in cleaned.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [int(item) for item in value]
        return value

    @field_validator("llm_provider")
    @classmethod
    def supported_llm_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"disabled", "openai", "anthropic"}:
            raise ValueError("LLM_PROVIDER must be disabled, openai, or anthropic")
        return normalized

    @field_validator("captcha_solver_provider")
    @classmethod
    def supported_captcha_solver_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"disabled", "capsolver", "anti_captcha", "anticaptcha"}:
            raise ValueError("CAPTCHA_SOLVER_PROVIDER must be disabled, capsolver, or anti_captcha")
        return "anti_captcha" if normalized == "anticaptcha" else normalized

    @model_validator(mode="after")
    def validate_configuration(self) -> Settings:
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        if self.captcha_solver_provider == "capsolver" and not self.capsolver_api_key:
            raise ValueError("CAPSOLVER_API_KEY is required when CAPTCHA_SOLVER_PROVIDER=capsolver")
        if self.captcha_solver_provider == "anti_captcha" and not self.anti_captcha_api_key:
            raise ValueError("ANTI_CAPTCHA_API_KEY is required when CAPTCHA_SOLVER_PROVIDER=anti_captcha")
        if self.auto_react_enabled and not self.master_encryption_key:
            raise ValueError("MASTER_ENCRYPTION_KEY is required when AUTO_REACT_ENABLED=true")
        if self.app_env.lower() == "production":
            missing = []
            if not self.admin_password_hash or "CHANGE_ME" in self.admin_password_hash:
                missing.append("ADMIN_PASSWORD_HASH")
            if len(self.session_secret_key) < 32 or "CHANGE_ME" in self.session_secret_key:
                missing.append("SESSION_SECRET_KEY")
            if not self.master_encryption_key or "CHANGE_ME" in self.master_encryption_key:
                missing.append("MASTER_ENCRYPTION_KEY")
            if not self.dashboard_cookie_secure:
                missing.append("DASHBOARD_COOKIE_SECURE=true")
            if missing:
                raise ValueError(f"unsafe production configuration: {', '.join(missing)}")
        return self

    def llm_models(self) -> tuple[str, str, str]:
        if self.llm_provider == "openai":
            defaults = ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")
        else:
            defaults = ("claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5")
        return (
            self.llm_cheap_model or defaults[0],
            self.llm_standard_model or defaults[1],
            self.llm_escalation_model or defaults[2],
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
