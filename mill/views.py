from datetime import date

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
    total_count = 0

    for log in logs:
        key = log.species.name
        row = by_species.setdefault(key, {"species": key, "count": 0, "log_v": 0.0, "lumber_v": 0.0})
        row["count"] += 1
        row["log_v"] += log.volume_m3
        row["lumber_v"] += log.lumber_volume_m3
        total_count += 1
        total_log_v += log.volume_m3
        total_lumber_v += log.lumber_volume_m3

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
            "total_yield": total_yield,
            "date_from": date_from.isoformat() if date_from else "",
            "date_to": date_to.isoformat() if date_to else "",
        },
    )
