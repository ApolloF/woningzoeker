"""Run a hard-coded no-submit browser smoke test for one public listing URL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.schemas import PrivateContactData  # noqa: E402
from app.services.reaction_browser import ReactionBrowser  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("url")
    parser.add_argument("--submission-id", type=int, default=900_000_001)
    args = parser.parse_args()
    contact = PrivateContactData(
        first_name="Test",
        last_name="Dryrun",
        initials="T.",
        email="dryrun@example.test",
        phone="0612345678",
        address="Teststraat",
        house_number="1",
        city="Groningen",
    )
    result = ReactionBrowser(get_settings()).react(
        source_name=args.source,
        listing_url=args.url,
        message="Veilige technische dry-run; dit bericht wordt niet verzonden.",
        contact=contact,
        credential=None,
        submission_id=args.submission_id,
        allow_submit=False,
    )
    print(
        json.dumps(
            {
                "state": result.state.value,
                "code": result.code,
                "field_names": result.field_names,
                "before_screenshot": result.before_screenshot,
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.state.value in {"DRY_RUN_STOPPED", "REVIEW_REQUIRED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
