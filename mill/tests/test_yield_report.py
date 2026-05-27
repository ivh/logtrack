from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from mill.models import Log, Lumber, Species


@pytest.fixture
def staff_client(db, client):
    user = User.objects.create_user("tom", password="x", is_staff=True)
    client.force_login(user)
    return client


@pytest.fixture
def species(db):
    return {
        "tall": Species.objects.create(name="Tall"),
        "gran": Species.objects.create(name="Gran"),
    }


def _log(species, d, l, mill_date):
    return Log.objects.create(
        species=species, diameter_cm=d, length_cm=l, mill_date=mill_date
    )


def test_yield_report_requires_staff(client, db):
    resp = client.get(reverse("mill:yield_report"))
    assert resp.status_code == 302
    assert "/admin/login" in resp["Location"]


def test_yield_report_empty(staff_client):
    resp = staff_client.get(reverse("mill:yield_report"))
    assert resp.status_code == 200
    assert "Inga stockar" in resp.content.decode()


def test_yield_report_aggregates_by_species(staff_client, species):
    today = date(2026, 5, 1)
    log_t = _log(species["tall"], 20, 200, today)
    Lumber.objects.create(log=log_t, thickness_mm=50, width_mm=100, length_mm=2000, count=2)
    log_g = _log(species["gran"], 30, 300, today)
    Lumber.objects.create(log=log_g, thickness_mm=25, width_mm=150, length_mm=3000, count=4)

    resp = staff_client.get(reverse("mill:yield_report"))
    assert resp.status_code == 200
    ctx = resp.context

    assert ctx["total_count"] == 2
    species_rows = {row["species"]: row for row in ctx["rows"]}
    assert species_rows["Tall"]["count"] == 1
    assert species_rows["Gran"]["count"] == 1
    assert species_rows["Tall"]["lumber_v"] == pytest.approx(0.020)
    assert species_rows["Gran"]["lumber_v"] == pytest.approx(0.045)
    assert ctx["total_lumber_v"] == pytest.approx(0.065)


def test_yield_report_date_filter(staff_client, species):
    _log(species["tall"], 20, 200, date(2026, 4, 1))
    _log(species["tall"], 20, 200, date(2026, 5, 15))

    resp = staff_client.get(
        reverse("mill:yield_report") + "?from=2026-05-01&to=2026-05-31"
    )
    assert resp.context["total_count"] == 1


def test_yield_report_lumber_v_shows_all_yield_calc_uses_measured_only(staff_client, species):
    today = date(2026, 5, 1)
    measured = Log.objects.create(species=species["tall"], diameter_cm=20, length_cm=200, mill_date=today)
    unmeasured = Log.objects.create(species=species["tall"], diameter_cm=None, length_cm=200, mill_date=today)
    Lumber.objects.create(log=measured, thickness_mm=50, width_mm=100, length_mm=2000, count=2)
    Lumber.objects.create(log=unmeasured, thickness_mm=50, width_mm=100, length_mm=2000, count=4)

    resp = staff_client.get(reverse("mill:yield_report"))
    ctx = resp.context
    assert ctx["total_count"] == 2
    row = next(r for r in ctx["rows"] if r["species"] == "Tall")
    # column shows ALL lumber: 0.020 + 0.040 = 0.060
    assert row["lumber_v"] == pytest.approx(0.060)
    # log_v from measured only
    assert row["log_v"] == pytest.approx(0.02 * 3.141592653589793)
    # yield uses lumber from measured logs only (0.020), divided by log_v
    assert row["yield_pct"] == pytest.approx(0.020 / (0.02 * 3.141592653589793) * 100)


def test_yield_report_lumber_v_shows_all_even_with_no_measured_logs(staff_client, species):
    today = date(2026, 5, 1)
    unmeasured = Log.objects.create(species=species["tall"], diameter_cm=None, length_cm=200, mill_date=today)
    Lumber.objects.create(log=unmeasured, thickness_mm=50, width_mm=100, length_mm=2000, count=3)

    resp = staff_client.get(reverse("mill:yield_report"))
    row = next(r for r in resp.context["rows"] if r["species"] == "Tall")
    assert row["lumber_v"] == pytest.approx(0.030)
    assert row["log_v"] == 0.0
    assert row["yield_pct"] is None


def test_yield_report_revenue_sums_only_sold(staff_client, species):
    today = date(2026, 5, 1)
    log = _log(species["tall"], 20, 200, today)
    Lumber.objects.create(
        log=log, thickness_mm=50, width_mm=100, length_mm=2000, count=4,
        unit_price_sek=Decimal("100.00"),
    )
    Lumber.objects.create(
        log=log, thickness_mm=25, width_mm=100, length_mm=2000, count=2,
        unit_price_sek=None,
    )

    resp = staff_client.get(reverse("mill:yield_report"))
    rows = {r["species"]: r for r in resp.context["rows"]}
    assert rows["Tall"]["revenue"] == Decimal("400.00")
    assert resp.context["total_revenue"] == Decimal("400.00")
