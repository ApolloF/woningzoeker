# Roadmap

## Phase 1 - safe vertical slice (complete)

- PostgreSQL, FastAPI, Alembic, Docker Compose, optional Caddy.
- Normalization, canonical deduplication, deterministic decisions/drafts, Telegram, and audit dashboard.

## Phase 2 - source coverage and LLM integration (complete)

- Ten registered sources covering every requested site.
- Browser retrieval for Huurwoningen, Pararius, and Funda; HTTP elsewhere.
- Campus Groningen successor handling and Pandomo legacy-feed monitoring.
- OpenAI/Anthropic structured output, model routing, fallback, and content caching.

## Phase 3 - guarded reaction automation (implemented, dry-run by default)

- Encrypted contact details, account credentials, and browser session state.
- Per-source login/action/form hints and a constrained allowlist field mapper.
- One-minute polling and immediate dispatch in the discovery transaction flow.
- Canonical idempotency, attempts, audit outcomes, and before/after screenshots.
- Hard review stops for CAPTCHA, legal declarations, payment, uploads, and unknown/sensitive fields.

## Phase 4 - operational safety and handoff (implemented)

- Persistent dashboard assistance queue with Telegram links, retry, manual-send confirmation, and skip.
- Recover stale attempts, retry bounded failures, and reconcile source jobs without restarting.
- Authenticated artifact access, automation-readiness reporting, database migration, and CI.
- Fixture coverage for discovery adapters and tests for rules, deduplication, LLM, reactions, and handoff.

## Phase 5 - deployment validation (operator action required)

- Complete a current dry-run evidence review for every source intended for `AUTO_REACT`.
- Enter the private contact profile, source accounts, LLM key, and optional Telegram credentials.
- Add an operator-specific PostgreSQL/artifact backup schedule and retention window.
- Observe live source drift and tune one-minute polling where site terms or stability require it.

## Phase 6 - controlled activation

- Enable only reviewed, non-binding viewing-request sources one at a time.
- Keep payments, contracts, attestations, identity verification, and document uploads permanently manual.
- Add newly discovered Groningen agencies only after inspecting discovery and reaction surfaces.
