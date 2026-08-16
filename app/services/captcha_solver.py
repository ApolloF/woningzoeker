from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class CaptchaSolver:
    """Interface to CapSolver and Anti-Captcha services for resolving browser challenges."""

    CAPSOLVER_BASE = "https://api.capsolver.com"
    ANTI_CAPTCHA_BASE = "https://api.anti-captcha.com"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def is_enabled(self) -> bool:
        provider = self.settings.captcha_solver_provider
        if provider == "capsolver":
            return bool(self.settings.capsolver_api_key)
        if provider == "anti_captcha":
            return bool(self.settings.anti_captcha_api_key)
        return False

    def solve_recaptcha_v2(
        self, website_url: str, sitekey: str, s_data: str | None = None
    ) -> str | None:
        if not self.is_enabled():
            return None
        if self.settings.captcha_solver_provider == "capsolver":
            task_payload: dict[str, Any] = {
                "type": "ReCaptchaV2TaskProxyLess",
                "websiteURL": website_url,
                "websiteKey": sitekey,
            }
            if s_data:
                task_payload["recaptchaDataSValue"] = s_data
            return self._solve_capsolver(task_payload)
        else:
            task_payload = {
                "type": "RecaptchaV2TaskProxyless",
                "websiteURL": website_url,
                "websiteKey": sitekey,
            }
            if s_data:
                task_payload["recaptchaDataSValue"] = s_data
            return self._solve_anti_captcha(task_payload)

    def solve_recaptcha_v3(
        self,
        website_url: str,
        sitekey: str,
        page_action: str | None = None,
        min_score: float = 0.7,
    ) -> str | None:
        if not self.is_enabled():
            return None
        if self.settings.captcha_solver_provider == "capsolver":
            task_payload: dict[str, Any] = {
                "type": "ReCaptchaV3TaskProxyLess",
                "websiteURL": website_url,
                "websiteKey": sitekey,
                "minScore": min_score,
            }
            if page_action:
                task_payload["pageAction"] = page_action
            return self._solve_capsolver(task_payload)
        else:
            task_payload = {
                "type": "RecaptchaV3TaskProxyless",
                "websiteURL": website_url,
                "websiteKey": sitekey,
                "minScore": min_score,
            }
            if page_action:
                task_payload["pageAction"] = page_action
            return self._solve_anti_captcha(task_payload)

    def solve_hcaptcha(self, website_url: str, sitekey: str) -> str | None:
        if not self.is_enabled():
            return None
        if self.settings.captcha_solver_provider == "capsolver":
            task_payload = {
                "type": "HCaptchaTaskProxyLess",
                "websiteURL": website_url,
                "websiteKey": sitekey,
            }
            return self._solve_capsolver(task_payload)
        else:
            task_payload = {
                "type": "HCaptchaTaskProxyless",
                "websiteURL": website_url,
                "websiteKey": sitekey,
            }
            return self._solve_anti_captcha(task_payload)

    def solve_turnstile(self, website_url: str, sitekey: str) -> str | None:
        if not self.is_enabled():
            return None
        if self.settings.captcha_solver_provider == "capsolver":
            task_payload = {
                "type": "AntiTurnstileTaskProxyLess",
                "websiteURL": website_url,
                "websiteKey": sitekey,
            }
            return self._solve_capsolver(task_payload)
        else:
            task_payload = {
                "type": "TurnstileTaskProxyless",
                "websiteURL": website_url,
                "websiteKey": sitekey,
            }
            return self._solve_anti_captcha(task_payload)

    def solve_image_captcha(self, image_base64: str) -> str | None:
        if not self.is_enabled():
            return None
        task_payload = {
            "type": "ImageToTextTask",
            "body": image_base64,
        }
        if self.settings.captcha_solver_provider == "capsolver":
            return self._solve_capsolver(task_payload)
        else:
            return self._solve_anti_captcha(task_payload)

    def _solve_capsolver(self, task: dict[str, Any]) -> str | None:
        api_key = self.settings.capsolver_api_key
        create_url = f"{self.CAPSOLVER_BASE}/createTask"
        result_url = f"{self.CAPSOLVER_BASE}/getTaskResult"
        timeout = self.settings.captcha_solver_timeout_seconds

        try:
            with httpx.Client(timeout=30.0) as client:
                res = client.post(create_url, json={"clientKey": api_key, "task": task})
                if res.status_code != 200:
                    logger.warning(
                        "CapSolver createTask failed",
                        extra={"context": {"status": res.status_code, "body": res.text[:200]}},
                    )
                    return None
                data = res.json()
                if data.get("errorId", 0) != 0:
                    logger.warning(
                        "CapSolver createTask error",
                        extra={"context": {"error": data.get("errorDescription")}},
                    )
                    return None
                task_id = data.get("taskId")
                if not task_id:
                    return None

                start_time = time.time()
                while time.time() - start_time < timeout:
                    poll_res = client.post(
                        result_url, json={"clientKey": api_key, "taskId": task_id}
                    )
                    poll_res.raise_for_status()
                    poll_data = poll_res.json()
                    if poll_data.get("errorId", 0) != 0:
                        logger.warning(
                            "CapSolver getTaskResult error",
                            extra={"context": {"error": poll_data.get("errorDescription")}},
                        )
                        return None
                    status = poll_data.get("status")
                    if status == "ready":
                        solution = poll_data.get("solution", {})
                        token = (
                            solution.get("gRecaptchaResponse")
                            or solution.get("token")
                            or solution.get("text")
                            or solution.get("response")
                        )
                        return str(token) if token else None
                    time.sleep(3)
                logger.warning("CapSolver task timed out", extra={"context": {"task_id": task_id}})
                return None
        except Exception as exc:
            logger.exception("CapSolver API request failed", extra={"context": {"error": str(exc)}})
            return None

    def _solve_anti_captcha(self, task: dict[str, Any]) -> str | None:
        api_key = self.settings.anti_captcha_api_key
        create_url = f"{self.ANTI_CAPTCHA_BASE}/createTask"
        result_url = f"{self.ANTI_CAPTCHA_BASE}/getTaskResult"
        timeout = self.settings.captcha_solver_timeout_seconds

        try:
            with httpx.Client(timeout=30.0) as client:
                res = client.post(create_url, json={"clientKey": api_key, "task": task})
                if res.status_code != 200:
                    logger.warning(
                        "Anti-Captcha createTask failed",
                        extra={"context": {"status": res.status_code, "body": res.text[:200]}},
                    )
                    return None
                data = res.json()
                if data.get("errorId", 0) != 0:
                    logger.warning(
                        "Anti-Captcha createTask error",
                        extra={"context": {"error": data.get("errorDescription")}},
                    )
                    return None
                task_id = data.get("taskId")
                if not task_id:
                    return None

                start_time = time.time()
                while time.time() - start_time < timeout:
                    poll_res = client.post(
                        result_url, json={"clientKey": api_key, "taskId": task_id}
                    )
                    poll_res.raise_for_status()
                    poll_data = poll_res.json()
                    if poll_data.get("errorId", 0) != 0:
                        logger.warning(
                            "Anti-Captcha getTaskResult error",
                            extra={"context": {"error": poll_data.get("errorDescription")}},
                        )
                        return None
                    status = poll_data.get("status")
                    if status == "ready":
                        solution = poll_data.get("solution", {})
                        token = (
                            solution.get("gRecaptchaResponse")
                            or solution.get("token")
                            or solution.get("text")
                        )
                        return str(token) if token else None
                    time.sleep(3)
                logger.warning("Anti-Captcha task timed out", extra={"context": {"task_id": task_id}})
                return None
        except Exception as exc:
            logger.exception("Anti-Captcha API request failed", extra={"context": {"error": str(exc)}})
            return None
