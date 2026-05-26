from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render

from .models import Log


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@staff_member_required
def yield_report(request):
    date_from = _parse_date(request.GET.get("from"))
    date_to = _parse_date(request.GET.get("to"))

    logs = Log.objects.select_related("species").prefetch_related("lumber")
    if date_from:
        logs = logs.filter(mill_date__gte=date_from)
    if date_to:
        logs = logs.filter(mill_date__lte=date_to)

    by_species: dict[str, dict] = {}
    total_log_v = 0.0
    total_lumber_v = 0.0
    total_revenue = Decimal("0")
    total_count = 0

    for log in logs:
        key = log.species.name
        row = by_species.setdefault(
            key,
            {"species": key, "count": 0, "log_v": 0.0, "lumber_v": 0.0, "revenue": Decimal("0")},
        )
        row["count"] += 1
        total_count += 1
        log_v = log.volume_m3
        if log_v is not None:
            log_lumber_v = log.lumber_volume_m3
            row["log_v"] += log_v
            row["lumber_v"] += log_lumber_v
            total_log_v += log_v
            total_lumber_v += log_lumber_v
        # Revenue is independent of measurement — every sold board counts.
        for lumber in log.lumber.all():
            if lumber.unit_price_sek is not None:
                row["revenue"] += lumber.revenue_sek
                total_revenue += lumber.revenue_sek

    rows = []
    for row in sorted(by_species.values(), key=lambda r: r["species"]):
        yield_pct = (row["lumber_v"] / row["log_v"] * 100) if row["log_v"] > 0 else None
        rows.append({**row, "yield_pct": yield_pct})

    total_yield = (total_lumber_v / total_log_v * 100) if total_log_v > 0 else None

    return render(
        request,
        "mill/yield_report.html",
        {
            **admin.site.each_context(request),
            "title": "Avkastningsrapport",
            "rows": rows,
            "total_count": total_count,
            "total_log_v": total_log_v,
            "total_lumber_v": total_lumber_v,
            "total_revenue": total_revenue,
            "total_yield": total_yield,
            "date_from": date_from.isoformat() if date_from else "",
            "date_to": date_to.isoformat() if date_to else "",
        },
    )


@staff_member_required
def blade_sessions(request):
    """Group logs into blade sessions by walking mill_date order.

    A session starts at a Log with fresh_blade_mounted=True and runs until
    the next such marker (exclusive). Logs before the first marker are
    grouped as 'okänd' (unattributed)."""
    logs = list(
        Log.objects.order_by("mill_date", "id")
    )

    sessions: list[dict] = []
    current: dict | None = None
    marked_count = 0
    for log in logs:
        if log.fresh_blade_mounted or current is None:
            if log.fresh_blade_mounted:
                marked_count += 1
                label = f"#{marked_count}"
            else:
                label = "okänd"
            current = {
                "label": label,
                "started": log.mill_date,
                "ended": log.mill_date,
                "logs": 0,
                "volume_m3": 0.0,
            }
            sessions.append(current)
        current["ended"] = log.mill_date
        current["logs"] += 1
        v = log.volume_m3
        if v is not None:
            current["volume_m3"] += v

    # mark the last session as open if its end matches the latest mill_date
    # and its label isn't 'okänd' (a marked session)
    if sessions and sessions[-1]["label"] != "okänd":
        sessions[-1]["open"] = True

    for s in sessions:
        days = (s["ended"] - s["started"]).days + 1 if s["logs"] else 0
        s["days"] = days

    sessions.reverse()  # most recent first

    return render(
        request,
        "mill/blade_sessions.html",
        {
            **admin.site.each_context(request),
            "title": "Klingobyten",
            "sessions": sessions,
        },
    )
