# Woningzoeker

A self-hosted rental-listing agent for Groningen. It polls eleven sources every minute, normalizes and
deduplicates homes, applies deterministic criteria, uses a structured-output LLM for textual safety
and Dutch drafting, and can prepare or submit a controlled viewing request through Chromium.

## Supported sources

| Source | Discovery | Reaction behavior |
|---|---|---|
| Bulten Vastgoed | HTTP | Current offer portal; a recognized non-binding form can be sent automatically |
| 123Wonen Groningen | HTTP | Public form; CAPTCHA becomes human review |
| Woldring Verhuur | HTTP | Managed encrypted account login |
| Huurwoningen.nl | Chromium | Managed login; CAPTCHA becomes human review |
| Maxx Groningen | HTTP | Guest form; terms checkbox remains manual |
| Gruno Vastgoed | HTTP | Public form; terms checkbox remains manual |
| Rotsvast Groningen | HTTP | Appointment form; CAPTCHA becomes human review |
| Pandomo | HTTP | Legacy feed; unverified/selection flows remain manual |
| Campus Groningen | HTTP + detail | Current Groningse Panden successor; registration remains manual |
| Pararius | Chromium | Managed encrypted account login |
| Funda rentals | Chromium | Public viewing-request form |

Every discovery adapter is isolated. A zero-result parse is a health failure, not a successful empty
run. Every connected source can be placed in `AUTO_REACT`; only recognized, non-binding forms are
submitted. CAPTCHA, login challenges, document uploads and legal confirmations are reported and never bypassed.

## Quick start

1. Copy `.env.example` to `.env` and generate secrets:

   ```powershell
   docker compose run --rm --no-deps app python -m app.cli generate-secrets
   ```

2. Fill the generated secrets and PostgreSQL password in `.env`.
3. Configure either OpenAI or Anthropic. Fully automatic reactions require a working LLM provider.
4. Start and verify:

   ```powershell
   docker compose up -d --build
   docker compose ps
   docker compose run --rm --no-deps app python scripts/smoke_sources.py
   ```

5. Open `http://127.0.0.1:8000/login`. In Settings, save private contact details, the editable
   applicant profile, source accounts and (where needed) an encrypted Google/2FA browser session.
   Then test individual listings with "Formulier als dry-run voorbereiden".

Keep `DRY_RUN=true` and `AUTO_REACT_ENABLED=false` while validating screenshots and audit outcomes.
After each desired source has passed dry-run, set that source to `AUTO_REACT`. Only then set
`DRY_RUN=false` and `AUTO_REACT_ENABLED=true` in `.env` and restart the app.

## Automatic reaction invariant

A new listing is considered immediately after its one-minute source poll. A real submission requires
all of the following:

- deterministic decision `AUTO_REACT`;
- a successful structured LLM result that did not request review;
- per-source mode `AUTO_REACT`, global `AUTO_REACT_ENABLED=true`, and `DRY_RUN=false`;
- configured encrypted contact details and, where required, account credentials;
- a recognized non-binding reaction form with only allowlisted fields;
- no earlier submission for the same deduplicated physical home.

CAPTCHA, document upload, payment/registration, identity/income fields, legal checkboxes, and unknown
required fields always stop in `REVIEW_REQUIRED`. Browser session cookies and passwords are encrypted
in PostgreSQL. Screenshots are private artifacts and can contain filled contact details.

## Human-assisted completion

When a browser cannot safely finish a reaction, it creates a durable item under **Hulp nodig**. The
item includes the listing, exact draft, reason, safe next steps, and any authenticated screenshots.
If Telegram is configured, the agent sends an immediate link to the source and assistance queue.

From the queue you can:

- retry after updating credentials or after a temporary source problem;
- open the original listing and complete a CAPTCHA, terms checkbox, document request, or changed form;
- confirm that the manual response was sent, preserving duplicate-response protection; or
- skip the response, recording the decision and preventing repeated prompts.

The scheduler keeps every other source running while an item waits for help. Failed non-consequential
browser attempts are retried every three minutes, up to `REACTION_MAX_ATTEMPTS`; stale in-progress
attempts are recovered after a restart. CAPTCHA is detected and escalated, never solved or bypassed.

For Pararius and Huurwoningen.nl, run
`.venv\Scripts\python.exe -m app.cli connect-source <source> --output playwright-data\<source>-session.json`
locally, complete Google and two-factor authentication yourself, and upload the JSON file through
**Settings > Accounts and Google/2FA sessions**. The imported browser session is encrypted; delete the
local JSON afterward. A stale session is sent to **Hulp nodig** instead of bypassing provider controls.

The dashboard and `GET /api/automation/readiness` show every activation blocker. A ready system has a
running scheduler, an enabled LLM provider, encrypted contact details, at least one source in
`AUTO_REACT`, `AUTO_REACT_ENABLED=true`, and `DRY_RUN=false`.

## LLM boundary

OpenAI uses the Responses API with strict JSON Schema and `store=false`. Anthropic uses the Messages
API with structured output. The LLM receives public listing data, allowlisted applicant facts, and the
explicitly configured financial and guarantor wording used to tailor a message. Guarantor wording is
only requested when the offer mentions a guarantor or hard income requirement. It never receives
credentials, cookies, the listing URL, current street address, or private form-contact values. It can
downgrade an automatic candidate, but cannot promote a deterministic rejection.

## Development and verification

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
ruff check .
mypy app
pytest --cov=app
```

CI runs the same lint, type-check, and test gates on every push and pull request.

See [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md), and [ROADMAP.md](ROADMAP.md).
