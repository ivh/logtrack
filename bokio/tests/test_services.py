from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from bokio.exceptions import BokioAuthError
from bokio.services import (
    create_draft_for_lumber,
    fetch_invoice_info,
    list_draft_invoices,
    push_lumber_to_invoice,
)
from mill.models import Log, Species
from mill.tests.helpers import make_lumber


@pytest.fixture
def lumber(db):
    sp = Species.objects.create(name="Tall")
    log = Log.objects.create(species=sp, diameter_cm=20, length_cm=300, mill_date=date(2026, 5, 1))
    return make_lumber(
        log, count=4, thickness_mm=50, width_mm=100, length_mm=3000,
        unit_price_sek=Decimal("160.00"),
    )


def test_push_refuses_unsold(lumber):
    lumber.unit_price_sek = None
    lumber.save()
    with pytest.raises(ValueError, match="osålt"):
        push_lumber_to_invoice(lumber, "inv-1")


def test_push_maps_fields_and_persists_ids(lumber):
    with patch("bokio.services.get_client") as gc:
        gc.return_value.get_invoice.return_value = {
            "status": "draft", "customerRef": {"name": "Kund AB"},
        }
        gc.return_value.add_line_item.return_value = {"id": "li-1"}
        line_item_id, customer = push_lumber_to_invoice(lumber, "inv-1")

    gc.return_value.add_line_item.assert_called_once()
    invoice_id, payload = gc.return_value.add_line_item.call_args.args
    assert invoice_id == "inv-1"
    assert payload["quantity"] == 4
    assert payload["unitPrice"] == 160.00
    assert "50×100×3000mm" in payload["description"]
    assert "Tall" in payload["description"]

    assert line_item_id == "li-1"
    assert customer == "Kund AB"
    lumber.refresh_from_db()
    assert lumber.bokio_invoice_id == "inv-1"
    assert lumber.bokio_line_item_id == "li-1"


def test_push_refuses_when_invoice_not_draft(lumber):
    with patch("bokio.services.get_client") as gc:
        gc.return_value.get_invoice.return_value = {"status": "published"}
        with pytest.raises(ValueError, match="utkast"):
            push_lumber_to_invoice(lumber, "inv-1")
        gc.return_value.add_line_item.assert_not_called()


def test_push_refuses_when_already_pushed(lumber):
    lumber.bokio_invoice_id = "inv-1"
    lumber.bokio_line_item_id = "li-1"
    lumber.save()
    with patch("bokio.services.get_client") as gc:
        with pytest.raises(ValueError, match="redan kopplat"):
            push_lumber_to_invoice(lumber, "inv-1")
        gc.return_value.add_line_item.assert_not_called()


def test_push_propagates_client_error(lumber):
    with patch("bokio.services.get_client") as gc:
        gc.return_value.get_invoice.return_value = {"status": "draft"}
        gc.return_value.add_line_item.side_effect = BokioAuthError("nope")
        with pytest.raises(BokioAuthError):
            push_lumber_to_invoice(lumber, "inv-1")
    lumber.refresh_from_db()
    assert lumber.bokio_invoice_id == ""
    assert lumber.bokio_line_item_id == ""


def test_create_draft_refuses_unsold(lumber):
    lumber.unit_price_sek = None
    lumber.save()
    with pytest.raises(ValueError, match="osålt"):
        create_draft_for_lumber(lumber)


def test_create_draft_refuses_when_already_linked(lumber):
    lumber.bokio_invoice_id = "inv-existing"
    lumber.save()
    with patch("bokio.services.get_client") as gc:
        with pytest.raises(ValueError, match="redan kopplat"):
            create_draft_for_lumber(lumber)
        gc.return_value.create_draft_invoice.assert_not_called()


def test_create_draft_payload_and_persists_ids(lumber):
    with patch("bokio.services.get_client") as gc:
        # Bokio returns line-item id as a number on draft creation
        gc.return_value.create_draft_invoice.return_value = {
            "id": "inv-new",
            "lineItems": [{"id": 12345}],
        }
        invoice_id, line_item_id = create_draft_for_lumber(lumber)

    payload = gc.return_value.create_draft_invoice.call_args.args[0]
    assert "invoiceDate" in payload
    assert len(payload["lineItems"]) == 1
    line = payload["lineItems"][0]
    assert line["quantity"] == 4
    assert line["unitPrice"] == 160.00
    assert line["taxRate"] == 25
    assert line["itemType"] == "salesItem"

    assert invoice_id == "inv-new"
    assert line_item_id == "12345"
    lumber.refresh_from_db()
    assert lumber.bokio_invoice_id == "inv-new"
    assert lumber.bokio_line_item_id == "12345"


def test_fetch_invoice_info_normalizes_fields(db):
    with patch("bokio.services.get_client") as gc:
        gc.return_value.get_invoice.return_value = {
            "id": "inv-1",
            "status": "published",
            "customerRef": {"id": "c-1", "name": "Kund AB"},
            "invoiceNumber": "1234",
            "currency": "SEK",
            "totalAmount": 200,
            "paidAmount": 0,
            "dueDate": "2026-06-30",
        }
        info = fetch_invoice_info("inv-1")
    gc.return_value.get_invoice.assert_called_once_with("inv-1")
    assert info.status == "published"
    assert info.customer_name == "Kund AB"
    assert info.invoice_number == "1234"
    assert info.currency == "SEK"
    assert info.total_amount == 200
    assert info.due_date == "2026-06-30"


def test_fetch_invoice_info_handles_missing_customer(db):
    with patch("bokio.services.get_client") as gc:
        gc.return_value.get_invoice.return_value = {"id": "inv-1", "status": "draft"}
        info = fetch_invoice_info("inv-1")
    assert info.status == "draft"
    assert info.customer_name == ""
    assert info.invoice_number == ""
    assert info.total_amount is None


def test_list_draft_invoices_filters_and_labels(db):
    with patch("bokio.services.get_client") as gc:
        gc.return_value.list_invoices.return_value = {
            "items": [
                {
                    "id": "inv-1", "status": "draft",
                    "customerRef": {"name": "Kund AB"},
                    "invoiceDate": "2026-05-20", "totalAmount": 200, "currency": "SEK",
                },
                {"id": "inv-2", "status": "published", "customerRef": {"name": "Annan"}},
                {"id": "inv-3", "status": "draft", "invoiceDate": "2026-05-21"},
            ],
        }
        drafts = list_draft_invoices()
    gc.return_value.list_invoices.assert_called_once_with(status="draft", page_size=100)
    ids = [d.id for d in drafts]
    assert ids == ["inv-3", "inv-1"]  # published filtered out, newest first
    assert "Kund AB" in drafts[1].label
    assert "200 SEK" in drafts[1].label
    assert "(ingen kund)" in drafts[0].label  # missing customer fallback


def test_list_draft_invoices_newest_first_and_capped(db):
    items = [
        {"id": f"inv-{n:02d}", "status": "draft", "invoiceDate": f"2026-01-{n:02d}"}
        for n in range(1, 13)  # 12 drafts, 2026-01-01 .. 2026-01-12
    ]
    with patch("bokio.services.get_client") as gc:
        gc.return_value.list_invoices.return_value = {"items": items}
        drafts = list_draft_invoices()
    ids = [d.id for d in drafts]
    assert len(ids) == 10  # capped
    assert ids[0] == "inv-12"  # newest first
    assert ids[-1] == "inv-03"  # two oldest dropped
    assert "inv-01" not in ids and "inv-02" not in ids
