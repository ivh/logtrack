# logtrack

Small Django webapp for a one-person Woodmizer sawmill: log it in, lumber it out,
track drying status, and push sold lumber to Bokio as invoice line items.
Single user (Tom), used from desktop and phone. Swedish UI labels, English code.

## Stack

- Python 3.14, managed by **uv** (`uv run …`). No pip, no requirements.txt.
- Django 6.x, SQLite (`db.sqlite3` is committed — it's a one-user dataset).
- **django-unfold** themes the admin (mobile-friendly).
- The entire UI is the Django admin. No HTMX, no DRF, no SPA.
- `requests` for Bokio HTTP. `responses` (dev) for mocking it in tests.

## Apps

```
mill/    — core domain: Species, Log, Lumber, yield report view
bokio/   — Bokio API client + push service. No models; bokio fields live on Lumber.
logtrack/ — Django project (settings, urls)
```

## Domain model (mill)

- **`Species`** — small ref table (Tall, Gran, Björk, …).
- **`Log`** — top diameter (cm), length, source, mill_date, notes, `fresh_blade_mounted`.
  `diameter_cm` is **nullable** (we didn't measure old logs).
  `Log.volume_m3` returns `None` when diameter is missing; `Log.yield_pct` also `None`.
  `fresh_blade_mounted` is a single boolean marking the first log sawn on a
  newly-mounted blade. It's deliberately **not** a `Blade` model — we tried
  that and threw it out because the physical blades can't be told apart
  once off the mill, so individual identity was dead weight. Sessions are
  computed on the fly from the boolean by walking logs in mill_date order
  (see `mill.views.blade_sessions`).
- **`Lumber`** — batch of identical boards from one `Log`. FK to Log.
  - `status`: `green` / `dry` / `picked_up` / `delivered` / `used_farm` /
    `used_private`. **No `sold` status** — sold-ness is "has a price"
    (the post-sale statuses just say where it physically went).
    `status_changed_at` updated by `save()` when status flips (uses
    `from_db` to remember the loaded value).
  - `unit_price_sek` — **ex VAT**, nullable. `None` ⇒ not sold. Setting it is
    the act of marking the batch sold. Only sold lumber feeds revenue stats.
  - `bokio_invoice_id`, `bokio_line_item_id` — strings; set by the push/create
    actions. Always strings, even though Bokio sometimes returns line-item ids
    as ints (the service does `str()`).

## Pricing

Formula from `~/lumber_pricing.py`: base `46 SEK/m at 45×95mm`, scaled by
cross-section area and length. **The formula gives prices including 25% VAT.**
`Lumber.suggested_price_sek` divides by `1 + LUMBER_VAT_RATE` so the stored
price is ex VAT. Constants in `logtrack/settings.py`:

```
LUMBER_BASE_PRICE_SEK_PER_M = Decimal("46.00")   # inc 25% moms
LUMBER_BASE_DIM_W_MM = 95
LUMBER_BASE_DIM_T_MM = 45
LUMBER_VAT_RATE = Decimal("0.25")
```

The suggested price is shown read-only on the Lumber change page; the
**"Använd föreslaget pris"** button fills `unit_price_sek` from it. A bulk
action populates missing prices across selected rows.

## Bokio integration (push only)

- Auth: `BOKIO_TOKEN` + `BOKIO_COMPANY_ID` from env. Loaded from `.env` if
  present via a tiny inline loader in `settings.py` (no python-dotenv dep).
  `.env` is gitignored; `.env.example` documents the vars.
- Base URL default: **`https://api.bokio.se/v1`** — the bare `https://api.bokio.se`
  hits a sunset beta endpoint (410). The `/v1/` prefix is non-negotiable.
- Endpoints used:
  - `POST /companies/{id}/invoices` — create draft (`bokio.client.create_draft_invoice`)
  - `POST /companies/{id}/invoices/{id}/line-items` — add line (`add_line_item`)
- Bokio gotchas burned-in via tests:
  - `unitPrice` must be a JSON **number**, not a string.
  - New-draft `lineItems[i].id` can come back as an **int**; service coerces to str.
  - Line items require `itemType: "salesItem"`, `productType: "goods"`, `taxRate` (int %).
- Rate limit: 200 req/60s per token. Client retries once on 429 using
  `Bokio-RateLimit-RetryAfter`.
- Typed errors in `bokio.exceptions`: `BokioConfigError`, `BokioAuthError`,
  `BokioNotFound`, `BokioRateLimited`, `BokioError`. Admin actions catch
  `BokioError` and `message_user` it.

Workflow:
1. On a Lumber row, set the price (button or manually).
2. Click **"Skapa Bokio-utkast"** — Bokio creates a draft, returns its GUID,
   we store it on the row.
3. For additional lumber going on the same invoice, paste the GUID into
   `bokio_invoice_id` and click **"Skicka till Bokio"**.
4. Finish/publish the invoice in Bokio (we do not publish from logtrack).

## Admin notes

- All buttons on the Lumber change page live inside the **Pris & försäljning**
  fieldset (not in the page header). Implemented as readonly `format_html`
  display methods + explicit `get_urls()` registration. URL names:
  `admin:mill_lumber_use_suggested_price`, `_push_to_bokio`, `_create_bokio_draft`.
- `format_html("…")` with **no args** raises `TypeError`. Use `mark_safe(…)`
  for static HTML. We've hit this twice — tests now lock the "Utkast redan
  kopplat." branch.
- Unfold sidebar is hand-rolled (`UNFOLD.SIDEBAR.navigation` in settings).
  When you add a new model, add a sidebar entry there too — `show_all_applications`
  is `False`, so the auto-list isn't rendered.

## Yield report

- `/yield/` — staff-only view aggregated by species, with date range filter.
- Extends `admin/base_site.html` and gets `admin.site.each_context(request)`
  so the Unfold chrome wraps it.
- Unmeasured logs (no diameter): counted in "Stockar". Their volume is
  excluded from "Stock m³" and from the **Avkastning %** numerator/denominator
  (apples-to-apples). **"Virke m³" shows all lumber** including from
  unmeasured logs, so the column reflects real inventory; this means
  `lumber_v ≠ yield_numerator` when there's a mix. Revenue is also
  independent of measurement.

## Blade sessions report

- `/blades/` — staff-only, sidebar entry **Klingobyten**.
- Walks `Log` in mill_date order. Each log with `fresh_blade_mounted=True`
  opens a new numbered session (`#1`, `#2`, …). Logs before the first
  marker are grouped as `okänd`. Per session: from/to dates, days span,
  log count, m³ sawn (only measured logs contribute). Most recent first;
  the latest marked session is tagged "(pågående)".

## Settings & environment

Security-sensitive settings come from env (loaded from `.env` via the inline
loader in `settings.py`; real env vars win over `.env`). Defaults are
**safe-for-prod** — a misconfigured prod fails closed, local dev opts into
convenience via `.env`:

- `DJANGO_SECRET_KEY` — defaults to an obvious `django-insecure-` placeholder.
  The original committed key is **burned** — rotate, don't reuse.
- `DJANGO_DEBUG` — `1`/`true`/`yes` to enable; **defaults off**.
- `DJANGO_ALLOWED_HOSTS` — comma-separated; empty by default.

When `DEBUG` is off, a hardening block sets secure session/CSRF cookies, plus
`CSRF_TRUSTED_ORIGINS` (from `DJANGO_CSRF_TRUSTED_ORIGINS`, comma-sep) and
`SECURE_PROXY_SSL_HEADER` (only when `DJANGO_BEHIND_TLS_PROXY` is truthy — set
it behind a TLS-terminating reverse proxy). Secure cookies need HTTPS, so the
block is **skipped in DEBUG** to keep local HTTP dev working. HSTS /
SSL-redirect are intentionally left to the proxy. `.env.example` documents
every var (Django + Bokio).

## Running

```
cp .env.example .env      # then fill it in; local dev wants DJANGO_DEBUG=true
uv run python manage.py migrate
uv run python manage.py runserver
uv run pytest
```

`manage.py runserver` boots fine without `BOKIO_TOKEN` set — Bokio actions
just raise `BokioConfigError` until the env is populated.

## Tests

- `pytest-django`, settings module via `pyproject.toml`.
- 60+ tests; suite runs in ~2s.
- All Bokio HTTP is mocked via `responses` (`bokio/tests/test_client.py`) or
  by patching `bokio.services.get_client` (`bokio/tests/test_services.py`).
- Admin actions tested with `force_login`'d superuser + URL reverse.
- Don't add live Bokio calls — there's no sandbox env.

## Lint & pre-push hook

- `ruff` (dev dep) lints with its default E/F ruleset: `uv run ruff check .`.
- `.git/hooks/pre-push` runs `ruff check` + `pytest` and blocks the push on
  failure. It's **local-only** — `.git/hooks` isn't tracked, so it won't
  survive a fresh clone; recreate it by hand (or point `core.hooksPath` at a
  tracked dir) on a new machine.

## House rules (user preferences)

- No emojis in strings.
- Don't commit unless asked. Don't push or tag unless explicitly asked.
- Code comments only when the *why* is non-obvious.
- Don't make summary docs unless explicitly asked.
- Use `uv` for Python. Use `uv add` / `uv add --dev` for new deps.
