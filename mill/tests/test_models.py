import math
from datetime import date

import pytest

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
