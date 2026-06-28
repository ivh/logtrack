from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django import forms
from django.contrib.auth.models import User
from django.urls import reverse

from bokio.exceptions import BokioAuthError
from mill.models import Log, Species

from .helpers import make_lumber


@pytest.fixture
def staff_client(db, client):
    user = User.objects.create_superuser("tom", password="x", email="x@x.se")
    client.force_login(user)
    return client


@pytest.fixture
def lumber_pair(db):
    sp = Species.objects.create(name="Tall")
    log = Log.objects.create(species=sp, diameter_cm=20, length_cm=300, mill_date=date(2026, 5, 1))
    unpriced = make_lumber(log, count=2, thickness_mm=50, width_mm=100, length_mm=3000)
    priced = make_lumber(
        log, count=2, thickness_mm=50, width_mm=100, length_mm=3000,
        unit_price_sek=Decimal("999.00"),
    )
    return unpriced, priced


def _sources_formset(lumber) -> dict:
    """POST data for the LumberSource inline, echoing back existing rows."""
    sources = list(lumber.sources.all())
    data = {
        "sources-TOTAL_FORMS": str(len(sources)),
        "sources-INITIAL_FORMS": str(len(sources)),
        "sources-MIN_NUM_FORMS": "0",
        "sources-MAX_NUM_FORMS": "1000",
    }
    for i, s in enumerate(sources):
        data[f"sources-{i}-id"] = str(s.pk)
        data[f"sources-{i}-log"] = str(s.log_id)
        data[f"sources-{i}-count"] = str(s.count)
    return data


def _log_change_post(log) -> dict:
    """Minimal POST body for the Log change page (echoes required fields)."""
    return {
        "species": str(log.species_id),
        "length_cm": str(log.length_cm),
        "mill_date": log.mill_date.isoformat(),
        "lumber_sources-TOTAL_FORMS": "1",
        "lumber_sources-INITIAL_FORMS": "0",
        "lumber_sources-MIN_NUM_FORMS": "0",
        "lumber_sources-MAX_NUM_FORMS": "1000",
    }


def test_log_inline_creates_new_lumber_from_dims(staff_client, db):
    sp = Species.objects.create(name="Gran")
    log = Log.objects.create(species=sp, diameter_cm=25, length_cm=400, mill_date=date(2026, 6, 1))
    data = _log_change_post(log)
    data["lumber_sources-0-new_dims"] = "50x100x2000"
    data["lumber_sources-0-count"] = "7"
    resp = staff_client.post(reverse("admin:mill_log_change", args=[log.pk]), data)
    assert resp.status_code == 302
    src = log.lumber_sources.get()
    assert src.count == 7
    assert (src.lumber.thickness_mm, src.lumber.width_mm, src.lumber.length_mm) == (50, 100, 2000)


def test_log_inline_rejects_both_lumber_and_dims(staff_client, db):
    sp = Species.objects.create(name="Gran")
    log = Log.objects.create(species=sp, diameter_cm=25, length_cm=400, mill_date=date(2026, 6, 1))
    existing = make_lumber(log, count=1, thickness_mm=25, width_mm=50, length_mm=1000)
    data = _log_change_post(log)
    data["lumber_sources-0-lumber"] = str(existing.pk)
    data["lumber_sources-0-new_dims"] = "50x100x2000"
    data["lumber_sources-0-count"] = "3"
    resp = staff_client.post(reverse("admin:mill_log_change", args=[log.pk]), data)
    assert resp.status_code == 200  # re-rendered with errors
    assert log.lumber_sources.count() == 1  # only the pre-existing source


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
    assert any("Bokio-utkast" in m for m in msgs)


def test_push_to_bokio_happy_path(staff_client, lumber_pair):
    _, priced = lumber_pair
    priced.bokio_invoice_id = "inv-99"
    priced.save()
    with patch("bokio.services.get_client") as gc:
        gc.return_value.get_invoice.return_value = {
            "status": "draft", "customerRef": {"name": "Kund AB"},
        }
        gc.return_value.add_line_item.return_value = {"id": "li-99"}
        url = reverse("admin:mill_lumber_push_to_bokio", args=[priced.pk])
        resp = staff_client.get(url, follow=True)
    priced.refresh_from_db()
    assert priced.bokio_line_item_id == "li-99"
    msgs = [m.message for m in resp.context["messages"]]
    assert any("Kund AB" in m for m in msgs)


def test_push_to_bokio_refuses_when_already_pushed(staff_client, lumber_pair):
    _, priced = lumber_pair
    priced.bokio_invoice_id = "inv-99"
    priced.bokio_line_item_id = "li-99"
    priced.save()
    with patch("bokio.services.get_client") as gc:
        # follow=True renders the change page, which fetches the linked invoice
        gc.return_value.get_invoice.return_value = {"status": "draft"}
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


def test_change_page_shows_bokio_status_when_invoice_linked(staff_client, lumber_pair):
    _, priced = lumber_pair
    priced.bokio_invoice_id = "inv-99"
    priced.save()
    with patch("bokio.services.get_client") as gc:
        gc.return_value.get_invoice.return_value = {
            "status": "published",
            "customerRef": {"name": "Kund AB"},
            "invoiceNumber": "1234",
            "currency": "SEK",
            "totalAmount": 200,
            "paidAmount": 0,
            "dueDate": "2026-06-30",
        }
        url = reverse("admin:mill_lumber_change", args=[priced.pk])
        resp = staff_client.get(url)
    gc.return_value.get_invoice.assert_called_once_with("inv-99")
    assert resp.status_code == 200
    assert b"Publicerad" in resp.content
    assert b"Kund AB" in resp.content


def test_change_page_skips_bokio_fetch_without_invoice_id(staff_client, lumber_pair):
    _, priced = lumber_pair  # no bokio_invoice_id
    with patch("mill.admin.fetch_invoice_info") as fii:
        url = reverse("admin:mill_lumber_change", args=[priced.pk])
        resp = staff_client.get(url)
    assert resp.status_code == 200
    fii.assert_not_called()


def test_change_page_degrades_when_bokio_unreachable(staff_client, lumber_pair):
    _, priced = lumber_pair
    priced.bokio_invoice_id = "inv-99"
    priced.save()
    with patch("mill.admin.fetch_invoice_info", side_effect=BokioAuthError("ogiltig token")):
        url = reverse("admin:mill_lumber_change", args=[priced.pk])
        resp = staff_client.get(url)
    assert resp.status_code == 200
    assert b"Kunde inte h" in resp.content  # "Kunde inte hämta..."


def test_total_price_field_prefilled_on_existing_lumber(staff_client, lumber_pair):
    _, priced = lumber_pair  # priced has unit_price_sek=999.00, count=2
    url = reverse("admin:mill_lumber_change", args=[priced.pk])
    resp = staff_client.get(url)
    field = resp.context["adminform"].form.fields["total_price_sek"]
    assert field.initial == Decimal("1998.00")


def test_entering_total_derives_unit_price(staff_client, lumber_pair):
    unpriced, _ = lumber_pair  # count=2 (one source), no unit price
    url = reverse("admin:mill_lumber_change", args=[unpriced.pk])
    payload = {
        "thickness_mm": "50", "width_mm": "100", "length_mm": "3000",
        "status": unpriced.status, "location": "", "notes": "",
        "unit_price_sek": "", "total_price_sek": "1500", "bokio_invoice_id": "",
    }
    payload.update(_sources_formset(unpriced))
    resp = staff_client.post(url, payload)
    assert resp.status_code == 302, resp.content[:500]
    unpriced.refresh_from_db()
    # 1500 / 2 = 750.00 (count comes from the source rows, derived in save_related)
    assert unpriced.unit_price_sek == Decimal("750.00")


def test_entering_unit_only_does_not_get_clobbered_by_initial_total(staff_client, lumber_pair):
    _, priced = lumber_pair  # unit=999, count=2, total initial=1998
    url = reverse("admin:mill_lumber_change", args=[priced.pk])
    # User changes unit only; total field shows initial 1998 but wasn't touched
    payload = {
        "thickness_mm": "50", "width_mm": "100", "length_mm": "3000",
        "status": priced.status, "location": "", "notes": "",
        "unit_price_sek": "500.00", "total_price_sek": "1998.00",
        "bokio_invoice_id": "",
    }
    payload.update(_sources_formset(priced))
    resp = staff_client.post(url, payload)
    assert resp.status_code == 302, resp.content[:500]
    priced.refresh_from_db()
    # total was unchanged (still 1998, the initial) → unit stays as the user entered
    assert priced.unit_price_sek == Decimal("500.00")


def test_changelist_uses_dims_as_link(staff_client, lumber_pair):
    unpriced, _ = lumber_pair
    resp = staff_client.get(reverse("admin:mill_lumber_changelist"))
    assert resp.status_code == 200
    html = resp.content.decode()
    change_url = reverse("admin:mill_lumber_change", args=[unpriced.pk])
    assert "50×100×3000" in html  # combined dimensions column
    assert f'href="{change_url}"' in html  # dims links to the change page


def test_source_inline_renders_above_price_box(staff_client, lumber_pair):
    unpriced, _ = lumber_pair
    url = reverse("admin:mill_lumber_change", args=[unpriced.pk])
    html = staff_client.get(url).content.decode().lower()
    assert "stockandel" in html  # source-log inline heading
    assert "pris &amp; f" in html  # "Pris & försäljning" fieldset heading
    assert html.index("stockandel") < html.index("pris &amp; f")


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


def test_change_page_offers_draft_picker_before_push(staff_client, lumber_pair):
    _, priced = lumber_pair  # priced, not yet pushed (no line item)
    with patch("bokio.services.get_client") as gc:
        gc.return_value.list_invoices.return_value = {
            "items": [
                {
                    "id": "inv-aaa", "status": "draft",
                    "customerRef": {"name": "Kund AB"},
                    "invoiceDate": "2026-05-20", "totalAmount": 200, "currency": "SEK",
                },
            ],
        }
        url = reverse("admin:mill_lumber_change", args=[priced.pk])
        resp = staff_client.get(url)
    field = resp.context["adminform"].form.fields["bokio_invoice_id"]
    assert isinstance(field, forms.ChoiceField)
    values = [v for v, _ in field.choices]
    assert "inv-aaa" in values
    assert any("Kund AB" in str(label) for _, label in field.choices)


def test_picker_degrades_to_text_when_bokio_unreachable(staff_client, lumber_pair):
    _, priced = lumber_pair
    with patch("mill.admin.list_draft_invoices", side_effect=BokioAuthError("nej")):
        url = reverse("admin:mill_lumber_change", args=[priced.pk])
        resp = staff_client.get(url)
    field = resp.context["adminform"].form.fields["bokio_invoice_id"]
    assert not isinstance(field, forms.ChoiceField)


def test_picker_not_built_once_pushed(staff_client, lumber_pair):
    _, priced = lumber_pair
    priced.bokio_invoice_id = "inv-99"
    priced.bokio_line_item_id = "li-99"
    priced.save()
    with patch("mill.admin.list_draft_invoices") as ldi, patch("bokio.services.get_client") as gc:
        gc.return_value.get_invoice.return_value = {"status": "draft"}
        url = reverse("admin:mill_lumber_change", args=[priced.pk])
        resp = staff_client.get(url)
    ldi.assert_not_called()
    field = resp.context["adminform"].form.fields["bokio_invoice_id"]
    assert not isinstance(field, forms.ChoiceField)
