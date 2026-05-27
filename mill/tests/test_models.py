import math
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from mill.models import Log, Lumber, Species


@pytest.fixture
def species(db):
    return Species.objects.create(name="Tall", latin_name="Pinus sylvestris")


@pytest.fixture
def log(db, species):
    return Log.objects.create(
        species=species,
        diameter_cm=20,
        length_cm=200,
        source="hemma",
        mill_date=date(2026, 5, 1),
    )


def test_species_str(db):
    sp = Species.objects.create(name="Gran")
    assert str(sp) == "Gran"


def test_log_str(log):
    assert str(log) == f"#{log.pk} Tall 20x200cm"


def test_lumber_str(log):
    lumber = Lumber.objects.create(
        log=log, thickness_mm=50, width_mm=100, length_mm=2000, count=4
    )
    assert str(lumber) == "4x 50x100x2000mm"


def test_log_volume_m3(log):
    # cylinder: pi * r^2 * L, with r=0.10m, L=2.0m → 0.02*pi
    assert log.volume_m3 == pytest.approx(0.02 * math.pi)


def test_lumber_volume_m3(log):
    # 50 * 100 * 3000 mm^3 * count 2 = 30_000_000 mm^3 = 0.030 m^3
    lumber = Lumber.objects.create(
        log=log, thickness_mm=50, width_mm=100, length_mm=3000, count=2
    )
    assert lumber.volume_m3 == pytest.approx(0.030)


def test_log_lumber_volume_m3_empty(log):
    assert log.lumber_volume_m3 == 0.0


def test_log_lumber_volume_m3_sums_related(log):
    Lumber.objects.create(log=log, thickness_mm=50, width_mm=100, length_mm=2000, count=2)
    Lumber.objects.create(log=log, thickness_mm=25, width_mm=100, length_mm=2000, count=4)
    # 2*(50*100*2000)/1e9 + 4*(25*100*2000)/1e9 = 0.020 + 0.020 = 0.040
    assert log.lumber_volume_m3 == pytest.approx(0.040)


def test_log_yield_pct(log):
    Lumber.objects.create(log=log, thickness_mm=50, width_mm=100, length_mm=2000, count=2)
    # lumber 0.020 m^3 / log 0.02*pi m^3 * 100
    expected = 0.020 / (0.02 * math.pi) * 100
    assert log.yield_pct == pytest.approx(expected)


def test_log_yield_pct_no_lumber_is_zero(log):
    assert log.yield_pct == pytest.approx(0.0)


def test_log_yield_pct_none_when_zero_volume(species):
    log = Log(species=species, diameter_cm=0, length_cm=200, mill_date=date(2026, 5, 1))
    assert log.yield_pct is None


def test_log_volume_m3_none_when_diameter_missing(species):
    log = Log(species=species, diameter_cm=None, length_cm=200, mill_date=date(2026, 5, 1))
    assert log.volume_m3 is None


def test_log_yield_pct_none_when_diameter_missing(db, species):
    log = Log.objects.create(species=species, diameter_cm=None, length_cm=200, mill_date=date(2026, 5, 1))
    Lumber.objects.create(log=log, thickness_mm=50, width_mm=100, length_mm=2000, count=2)
    assert log.yield_pct is None


def test_lumber_status_changed_at_set_on_create(log):
    before = timezone.now()
    lumber = Lumber.objects.create(log=log, thickness_mm=50, width_mm=100, length_mm=2000)
    assert before <= lumber.status_changed_at <= timezone.now()


def test_lumber_status_changed_at_updates_when_status_changes(log):
    lumber = Lumber.objects.create(log=log, thickness_mm=50, width_mm=100, length_mm=2000)
    old_ts = timezone.now() - timedelta(days=5)
    Lumber.objects.filter(pk=lumber.pk).update(status_changed_at=old_ts)

    fresh = Lumber.objects.get(pk=lumber.pk)
    fresh.status = Lumber.Status.DRY
    fresh.save()
    assert fresh.status_changed_at > old_ts


def test_lumber_status_changed_at_stable_when_other_fields_change(log):
    lumber = Lumber.objects.create(log=log, thickness_mm=50, width_mm=100, length_mm=2000)
    old_ts = timezone.now() - timedelta(days=5)
    Lumber.objects.filter(pk=lumber.pk).update(status_changed_at=old_ts)

    fresh = Lumber.objects.get(pk=lumber.pk)
    fresh.location = "skjul"
    fresh.save()
    # status didn't change, so timestamp should be unchanged
    assert abs((fresh.status_changed_at - old_ts).total_seconds()) < 1


def test_lumber_days_in_status(log):
    lumber = Lumber.objects.create(log=log, thickness_mm=50, width_mm=100, length_mm=2000)
    Lumber.objects.filter(pk=lumber.pk).update(
        status_changed_at=timezone.now() - timedelta(days=7, hours=2)
    )
    fresh = Lumber.objects.get(pk=lumber.pk)
    assert fresh.days_in_status == 7


def test_lumber_status_sold_no_longer_exists():
    assert "sold" not in {v for v, _ in Lumber.Status.choices}


def test_suggested_price_sek_ex_vat_at_base_dim(log):
    # 45×95×1000 mm at base dim → 46 SEK inc VAT per metre; ex VAT = 46/1.25 = 36.80
    lumber = Lumber(log=log, thickness_mm=45, width_mm=95, length_mm=1000, count=1)
    assert lumber.suggested_price_sek == Decimal("36.80")


def test_suggested_price_sek_scales_with_dim_and_length(log):
    # 50×100×3000 mm — inc VAT = 46/(45*95) * 50*100*3 ≈ 161.40 → ex VAT ≈ 129.12
    lumber = Lumber(log=log, thickness_mm=50, width_mm=100, length_mm=3000, count=1)
    expected = (Decimal("46.00") * Decimal(50) * Decimal(100) / Decimal(45 * 95) * Decimal(3)) / Decimal("1.25")
    assert lumber.suggested_price_sek == expected.quantize(Decimal("0.01"))


def test_is_sold_false_when_no_price(log):
    lumber = Lumber.objects.create(log=log, thickness_mm=50, width_mm=100, length_mm=2000)
    assert lumber.is_sold is False
    assert lumber.revenue_sek is None


def test_is_sold_true_and_revenue_when_price_set(log):
    lumber = Lumber.objects.create(
        log=log, thickness_mm=50, width_mm=100, length_mm=2000, count=4,
        unit_price_sek=Decimal("125.50"),
    )
    assert lumber.is_sold is True
    assert lumber.revenue_sek == Decimal("502.00")
