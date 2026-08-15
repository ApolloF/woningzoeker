from __future__ import annotations

import argparse
import json
import sys

from app.adapters import ALL_ADAPTERS


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test rental source adapters")
    parser.add_argument("sources", nargs="*", help="Optional source_name filters")
    args = parser.parse_args()
    selected = set(args.sources)
    failures = 0

    for adapter_type in ALL_ADAPTERS:
        adapter = adapter_type()
        if selected and adapter.source_name not in selected:
            continue
        try:
            listings = adapter.discover()
            available = [listing for listing in listings if listing.is_available]
            first = listings[0] if listings else None
            print(
                json.dumps(
                    {
                        "source": adapter.source_name,
                        "status": "ok" if listings else "empty",
                        "count": len(listings),
                        "available": len(available),
                        "first_title": first.title if first else None,
                        "first_city": first.city if first else None,
                        "first_rent": str(first.rent_total) if first and first.rent_total else None,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if not listings:
                failures += 1
        except Exception as exc:  # noqa: BLE001 - smoke runner must continue across sources
            failures += 1
            print(
                json.dumps(
                    {
                        "source": adapter.source_name,
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:500],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
