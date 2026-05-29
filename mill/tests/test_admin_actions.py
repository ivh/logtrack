from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from mill.models import Log, Lumber, Species


@pytest.fixture
def staff_client(db, client):
    user = User.objects.create_superuser("tom", password="x", email="x@x.se")
    client.force_login(user)
    return client


@pytest.fixture
def lumber_pair(db):
    sp = Species.objects.create(name="Tall")
    log = Log.objects.create(species=sp, diameter_cm=20, length_cm=300, mill_date=date(2026, 5, 1))
    unpriced = Lumber.objects.create(log=log, thickness_mm=50, width_mm=100, length_mm=3000, count=2)
    priced = Lumber.objects.create(
        log=log, thickness_mm=50, width_mm=100, length_mm=3000, count=2,
        unit_price_sek=Decimal("999.00"),
    )
    return unpriced, priced


def test_bulk_apply_suggested_price_only_fills_empty(staff_client, lumber_pair):
    unpriced, priced = lumber_pair
    resp = staff_client.post(
        reverse("admin:mill_lumber_changelist"),
        {
            "action": "apply_suggested_price_action",
            "_selected_action": [unpriced.pk, priced.pk],
        },
    )
    assert resp.status_code == 302
    unpriced.refresh_from_db()
    priced.refresh_from_db()
    assert unpriced.unit_price_sek is not None
    assert priced.unit_price_sek == Decimal("999.00")


def test_use_suggested_price_detail_action(staff_client, lumber_pair):
    unpriced, _ = lumber_pair
    url = reverse("admin:mill_lumber_use_suggested_price", args=[unpriced.pk])
    resp = staff_client.get(url)
    assert resp.status_code == 302
    unpriced.refresh_from_db()
    assert unpriced.unit_price_sek == unpriced.suggested_price_sek


def test_push_to_bokio_refuses_without_invoice_id(staff_client, lumber_pair):
    _, priced = lumber_pair
    url = reverse("admin:mill_lumber_push_to_bokio", args=[priced.pk])
    resp = staff_client.get(url, follow=True)
    msgs = [m.message for m in resp.context["messages"]]
    assert any("Bokio-faktura-id" in m for m in msgs)


def test_push_to_bokio_happy_path(staff_client, lumber_pair):
    _, priced = lumber_pair
    priced.bokio_invoice_id = "inv-99"
    priced.save()
    with patch("bokio.services.get_client") as gc:
        gc.return_value.add_line_item.return_value = {"id": "li-99"}
        url = reverse("admin:mill_lumber_push_to_bokio", args=[priced.pk])
        staff_client.get(url)
    priced.refresh_from_db()
    assert priced.bokio_line_item_id == "li-99"


def test_push_to_bokio_refuses_when_already_pushed(staff_client, lumber_pair):
    _, priced = lumber_pair
    priced.bokio_invoice_id = "inv-99"
    priced.bokio_line_item_id = "li-99"
    priced.save()
    with patch("bokio.services.get_client") as gc:
        url = reverse("admin:mill_lumber_push_to_bokio", args=[priced.pk])
        resp = staff_client.get(url, follow=True)
    gc.return_value.add_line_item.assert_not_called()
    msgs = [m.message for m in resp.context["messages"]]
    assert any("redan kopplat" in m for m in msgs)


def test_push_button_hidden_once_pushed(staff_client, lumber_pair):
    _, priced = lumber_pair
    priced.bokio_invoice_id = "inv-99"
    priced.bokio_line_item_id = "li-99"
    priced.save()
    url = reverse("admin:mill_lumber_change", args=[priced.pk])
    resp = staff_client.get(url)
    assert resp.status_code == 200
    assert b"Redan skickat till Bokio" in resp.content


def test_create_bokio_draft_happy_path(staff_client, lumber_pair):
    _, priced = lumber_pair
    with patch("bokio.services.get_client") as gc:
        gc.return_value.create_draft_invoice.return_value = {
            "id": "inv-new",
            "lineItems": [{"id": "li-new"}],
        }
        url = reverse("admin:mill_lumber_create_bokio_draft", args=[priced.pk])
        staff_client.get(url)
    priced.refresh_from_db()
    assert priced.bokio_invoice_id == "inv-new"
    assert priced.bokio_line_item_id == "li-new"


def test_create_bokio_draft_refuses_when_already_linked(staff_client, lumber_pair):
    _, priced = lumber_pair
    priced.bokio_invoice_id = "inv-existing"
    priced.save()
    with patch("bokio.services.get_client") as gc:
        url = reverse("admin:mill_lumber_create_bokio_draft", args=[priced.pk])
        staff_client.get(url)
    gc.return_value.create_draft_invoice.assert_not_called()
    priced.refresh_from_db()
    assert priced.bokio_invoice_id == "inv-existing"


def test_create_bokio_draft_refuses_when_unpriced(staff_client, lumber_pair):
    unpriced, _ = lumber_pair
    with patch("bokio.services.get_client") as gc:
        url = reverse("admin:mill_lumber_create_bokio_draft", args=[unpriced.pk])
        staff_client.get(url)
    gc.return_value.create_draft_invoice.assert_not_called()


def test_total_price_field_prefilled_on_existing_lumber(staff_client, lumber_pair):
    _, priced = lumber_pair  # priced has unit_price_sek=999.00, count=2
    url = reverse("admin:mill_lumber_change", args=[priced.pk])
    resp = staff_client.get(url)
    field = resp.context["adminform"].form.fields["total_price_sek"]
    assert field.initial == Decimal("1998.00")


def test_entering_total_derives_unit_price(staff_client, lumber_pair):
    unpriced, _ = lumber_pair  # count=2, no unit price
    url = reverse("admin:mill_lumber_change", args=[unpriced.pk])
    base = staff_client.get(url).context["adminform"].form.initial
    payload = {**base, "total_price_sek": "1500", "unit_price_sek": ""}
    # Carry over the required fields from the form
    payload.update({
        "log": str(unpriced.log.pk),
        "thickness_mm": "50", "width_mm": "100", "length_mm": "3000",
        "count": "2", "status": unpriced.status,
        "lumber_set-TOTAL_FORMS": "0", "lumber_set-INITIAL_FORMS": "0",
    })
    payload.pop("status_changed_at", None)
    resp = staff_client.post(url, payload)
    assert resp.status_code == 302, resp.content[:500]
    unpriced.refresh_from_db()
    # 1500 / 2 = 750.00
    assert unpriced.unit_price_sek == Decimal("750.00")


def test_entering_unit_only_does_not_get_clobbered_by_initial_total(staff_client, lumber_pair):
    _, priced = lumber_pair  # unit=999, count=2, total initial=1998
    url = reverse("admin:mill_lumber_change", args=[priced.pk])
    # User changes unit only; total field shows initial 1998 but wasn't touched
    payload = {
        "log": str(priced.log.pk),
        "thickness_mm": "50", "width_mm": "100", "length_mm": "3000",
        "count": "2", "status": priced.status,
        "unit_price_sek": "500.00", "total_price_sek": "1998.00",
        "bokio_invoice_id": "", "notes": "", "location": "",
        "lumber_set-TOTAL_FORMS": "0", "lumber_set-INITIAL_FORMS": "0",
    }
    resp = staff_client.post(url, payload)
    assert resp.status_code == 302, resp.content[:500]
    priced.refresh_from_db()
    # total was unchanged (still 1998, the initial) → unit stays as the user entered
    assert priced.unit_price_sek == Decimal("500.00")


def test_lumber_change_page_renders_when_linked_to_bokio(staff_client, lumber_pair):
    """Regression: 'Utkast redan kopplat.' branch must not crash format_html."""
    _, priced = lumber_pair
    priced.bokio_invoice_id = "inv-linked"
    priced.bokio_line_item_id = "12345"
    priced.save()
    url = reverse("admin:mill_lumber_change", args=[priced.pk])
    resp = staff_client.get(url)
    assert resp.status_code == 200
    assert b"Utkast redan kopplat" in resp.content
