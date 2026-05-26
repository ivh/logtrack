from datetime import date

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from mill.models import Log, Species


@pytest.fixture
def staff_client(db, client):
    user = User.objects.create_user("tom", password="x", is_staff=True)
    client.force_login(user)
    return client


@pytest.fixture
def species(db):
    return Species.objects.create(name="Tall")


def _log(species, mill_date, fresh=False, diameter=20, length=200):
    return Log.objects.create(
        species=species, diameter_cm=diameter, length_cm=length,
        mill_date=mill_date, fresh_blade_mounted=fresh,
    )


def test_blade_sessions_requires_staff(client, db):
    resp = client.get(reverse("mill:blade_sessions"))
    assert resp.status_code == 302


def test_blade_sessions_empty(staff_client):
    resp = staff_client.get(reverse("mill:blade_sessions"))
    assert resp.status_code == 200
    assert resp.context["sessions"] == []


def test_blade_sessions_logs_before_first_marker_are_unattributed(staff_client, species):
    _log(species, date(2026, 1, 5))  # unattributed
    _log(species, date(2026, 1, 10), fresh=True)
    _log(species, date(2026, 1, 12))

    resp = staff_client.get(reverse("mill:blade_sessions"))
    sessions = resp.context["sessions"]
    # most recent first
    assert sessions[0]["label"] == "#1"
    assert sessions[0]["logs"] == 2
    assert sessions[0]["started"] == date(2026, 1, 10)
    assert sessions[0]["ended"] == date(2026, 1, 12)
    assert sessions[1]["label"] == "okänd"
    assert sessions[1]["logs"] == 1


def test_blade_sessions_two_marks_two_sessions(staff_client, species):
    _log(species, date(2026, 1, 1), fresh=True)
    _log(species, date(2026, 1, 5))
    _log(species, date(2026, 2, 1), fresh=True)
    _log(species, date(2026, 2, 3))

    sessions = staff_client.get(reverse("mill:blade_sessions")).context["sessions"]
    # most recent first
    assert sessions[0]["logs"] == 2
    assert sessions[0]["started"] == date(2026, 2, 1)
    assert sessions[1]["logs"] == 2
    assert sessions[1]["started"] == date(2026, 1, 1)


def test_blade_sessions_volume_sums_only_measured(staff_client, species):
    _log(species, date(2026, 1, 1), fresh=True, diameter=20, length=200)
    _log(species, date(2026, 1, 5), diameter=None)  # unmeasured, contributes 0

    sessions = staff_client.get(reverse("mill:blade_sessions")).context["sessions"]
    assert sessions[0]["logs"] == 2
    # only the measured log contributes
    assert sessions[0]["volume_m3"] == pytest.approx(0.02 * 3.141592653589793)


def test_blade_sessions_open_session_marked(staff_client, species):
    _log(species, date(2026, 1, 1), fresh=True)
    sessions = staff_client.get(reverse("mill:blade_sessions")).context["sessions"]
    assert sessions[0]["open"] is True
