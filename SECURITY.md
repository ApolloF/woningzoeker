# Security model

## Trust boundaries

The dashboard is assumed internet-accessible. Caddy terminates TLS; FastAPI trusts no identity
headers. Sessions are signed HTTP-only SameSite cookies, production requires `Secure`, and every
mutating dashboard form uses a session-bound CSRF token. PostgreSQL and Chromium debugging remain
private. Containers use `no-new-privileges`.

## Secrets, private data, and LLM privacy

- Passwords, tokens, LLM keys, cookies, and master keys must never be committed.
- Source credentials, browser storage state, and contact details are Fernet-encrypted with an external
  master key. Existing plaintext values are never rendered back into the dashboard.
- Logs and audits contain only outcome codes, field names, provider/model names, and error classes.
- Reaction screenshots are private artifacts and may contain contact values; restrict access and use a
  retention/backup policy appropriate for personal data.
- The LLM allowlist excludes credentials, cookies, URLs, current address, and private contact fields.
  It intentionally includes only the financial and guarantor wording explicitly managed in the
  applicant profile. OpenAI requests set `store=false`.

## Browser and challenge safety

The reaction browser only fills allowlisted identity/contact/message fields on a recognized form. It
does not solve or outsource CAPTCHA. File uploads, payment or registration actions, identity/income
fields, unknown required fields, and personal terms/privacy checkboxes stop for human review. A stale
account session may be refreshed with encrypted credentials; a login challenge also stops for review.

Human help remains authenticated and explicit. Assistance items link to the source, never expose a
Playwright debugging port, and require the normal dashboard session plus CSRF validation for retry,
confirm, or skip. Screenshot files are served only after authentication and only when their resolved
path remains inside the configured artifact directory. The user, not the agent, completes CAPTCHA,
accepts terms, supplies documents, or answers sensitive questions.

## Submission invariant

A real submission requires per-source `AUTO_REACT`, global `AUTO_REACT_ENABLED=true`,
`DRY_RUN=false`, deterministic `AUTO_REACT`, a successful non-review LLM result, configured private
data, and no earlier canonical submission. A unique database constraint prevents cross-source double
responses to the same physical home. Once a submit click is executed it is recorded as sent even when
confirmation text is ambiguous, preventing an unsafe automatic retry.

## Deployment checklist

- Replace every `CHANGE_ME`, enable secure cookies, and expose only Caddy ports 80/443.
- Keep dry-run/global auto-react off until each desired source has current screenshot evidence.
- Restrict Telegram controls to configured numeric user IDs.
- Back up PostgreSQL, artifacts, and the external master key separately.
- Review applicable site terms and source health after adapter/browser upgrades.
- Review and remove old reaction artifacts according to your personal-data retention policy.
