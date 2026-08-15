from __future__ import annotations

import argparse
import secrets

from cryptography.fernet import Fernet
from pwdlib import PasswordHash


def generate_secrets() -> None:
    password = secrets.token_urlsafe(18)
    password_hash = PasswordHash.recommended().hash(password)
    print(f"Generated dashboard password (store it now): {password}")
    # Single quotes prevent Docker Compose from expanding the dollar signs in an Argon2 hash.
    print(f"ADMIN_PASSWORD_HASH='{password_hash}'")
    print(f"SESSION_SECRET_KEY={secrets.token_urlsafe(48)}")
    print(f"MASTER_ENCRYPTION_KEY={Fernet.generate_key().decode()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["generate-secrets"])
    args = parser.parse_args()
    if args.command == "generate-secrets":
        generate_secrets()


if __name__ == "__main__":
    main()
