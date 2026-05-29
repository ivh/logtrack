# logtrack

> "Log" here means **tree logs** — the round timber that goes into a sawmill —
> not log *files*. This is sawmill bookkeeping, not an observability tool.

A small personal web app for running a one-person [Woodmizer](https://woodmizer.com)
sawmill. A log comes in, gets sawn into lumber, the lumber dries and eventually
gets sold or used. logtrack keeps track of all of that, and can push sold lumber
into [Bokio](https://www.bokio.se) (Swedish accounting software) as invoice
line items.

It's built for one person (the mill's owner) and used from both a laptop and a
phone. The interface is in Swedish; the code is in English.

## What it tracks

- **Stockar (Logs)** — incoming tree logs: species, diameter, length, where they
  came from, when they were milled.
- **Virke (Lumber)** — batches of boards sawn from a log: dimensions, drying
  status, price, where each batch ended up.
- **Avkastning (Yield)** — how much usable lumber came out of the logs, by species.
- **Klingobyten (Blade changes)** — how many logs each sawmill blade got through
  before it was swapped.

## Tech, briefly

It's a [Django](https://www.djangoproject.com) app with a SQLite database. There
is no custom front-end — the whole UI is the Django admin, themed with
[django-unfold](https://unfoldadmin.com) so it works on a phone. Python is
managed with [uv](https://docs.astral.sh/uv).

## Running it

```
cp .env.example .env          # fill in values; set DJANGO_DEBUG=true for local dev
uv run python manage.py migrate
uv run python manage.py runserver
```

Then open http://127.0.0.1:8000/admin/. Bokio features stay dormant until you
add Bokio credentials to `.env`; everything else works without them.

Developer notes (domain model, Bokio quirks, conventions) live in `CLAUDE.md`.
