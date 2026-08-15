from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path

from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright
from pwdlib import PasswordHash


def generate_secrets() -> None:
    password = secrets.token_urlsafe(18)
    password_hash = PasswordHash.recommended().hash(password)
    print(f"Generated dashboard password (store it now): {password}")
    print(f"ADMIN_PASSWORD_HASH='{password_hash}'")
    print(f"SESSION_SECRET_KEY={secrets.token_urlsafe(48)}")
    print(f"MASTER_ENCRYPTION_KEY={Fernet.generate_key().decode()}")


def connect_source(source_name: str, output: Path | None = None) -> None:
    """Capture a user-completed Google/2FA session without storing its password."""
    from app.services.reaction_browser import REACTION_SPECS

    spec = REACTION_SPECS.get(source_name)
    if spec is None or not spec.login_url:
        raise SystemExit(f"{source_name} does not have an interactive account connection flow")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(spec.login_url, wait_until="domcontentloaded")
        input(
            "Complete Google sign-in and any two-factor check in the opened browser, then press Enter here. "
        )
        state = dict(context.storage_state())
        browser.close()

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        print(f"Browser session exported to {output}. Upload it on the Settings page.")
        return

    from app.db import SessionLocal
    from app.seed import seed_defaults
    from app.services.reactions import ReactionService

    with SessionLocal() as db:
        seed_defaults(db)
    ReactionService().save_browser_session(source_name, state)
    print(f"Encrypted browser session saved for {source_name}.")


def main() -> None:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("generate-secrets")
    connect = subcommands.add_parser("connect-source")
    connect.add_argument("source", choices=["huurwoningen", "pararius"])
    connect.add_argument(
        "--output",
        type=Path,
        help="Export a temporary JSON file that can be uploaded through Settings.",
    )
    args = parser.parse_args()
    if args.command == "generate-secrets":
        generate_secrets()
    elif args.command == "connect-source":
        connect_source(args.source, args.output)


if __name__ == "__main__":
    main()
