from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from django.conf import settings

from .client import get_client

if TYPE_CHECKING:
    from mill.models import Lumber


def _line_payload(lumber: Lumber) -> dict:
    vat_pct = int(settings.LUMBER_VAT_RATE * 100)
    return {
        "description": (
            f"{lumber.count}st {lumber.thickness_mm}×{lumber.width_mm}"
            f"×{lumber.length_mm}mm {lumber.log.species}"
        ),
        "itemType": "salesItem",
        "productType": "goods",
        "quantity": lumber.count,
        "unitPrice": float(lumber.unit_price_sek),
        "taxRate": vat_pct,
    }


def push_lumber_to_invoice(lumber: Lumber, invoice_id: str) -> str:
    if lumber.unit_price_sek is None:
        raise ValueError("kan inte skicka osålt virke")
    if lumber.bokio_line_item_id:
        raise ValueError("virket är redan kopplat till ett Bokio-radobjekt")

    response = get_client().add_line_item(invoice_id, _line_payload(lumber))
    line_item_id = str(response.get("id") or response.get("lineItemId") or "")

    lumber.bokio_invoice_id = invoice_id
    lumber.bokio_line_item_id = line_item_id
    lumber.save(update_fields=["bokio_invoice_id", "bokio_line_item_id"])
    return line_item_id


def create_draft_for_lumber(lumber: Lumber) -> tuple[str, str]:
    """Create a draft invoice on Bokio with this lumber as the first line item.

    Returns (invoice_id, line_item_id) and persists them on the lumber row.
    """
    if lumber.unit_price_sek is None:
        raise ValueError("kan inte skapa utkast för osålt virke")
    if lumber.bokio_invoice_id:
        raise ValueError("virket är redan kopplat till en Bokio-faktura")

    payload = {
        "invoiceDate": date.today().isoformat(),
        "lineItems": [_line_payload(lumber)],
    }
    response = get_client().create_draft_invoice(payload)
    invoice_id = str(response.get("id", ""))
    line_items = response.get("lineItems") or []
    line_item_id = str(line_items[0].get("id", "")) if line_items else ""

    lumber.bokio_invoice_id = invoice_id
    lumber.bokio_line_item_id = line_item_id
    lumber.save(update_fields=["bokio_invoice_id", "bokio_line_item_id"])
    return invoice_id, line_item_id


@dataclass(frozen=True)
class InvoiceInfo:
    status: str
    customer_name: str
    invoice_number: str
    currency: str
    total_amount: float | None
    paid_amount: float | None
    due_date: str


def fetch_invoice_info(invoice_id: str) -> InvoiceInfo:
    """Read-only fetch of the live invoice from Bokio, normalized for display.

    Raises BokioError (or a subclass) when Bokio is unreachable/misconfigured.
    """
    data = get_client().get_invoice(invoice_id)
    customer = data.get("customerRef") or {}
    return InvoiceInfo(
        status=str(data.get("status") or ""),
        customer_name=str(customer.get("name") or ""),
        invoice_number=str(data.get("invoiceNumber") or ""),
        currency=str(data.get("currency") or ""),
        total_amount=data.get("totalAmount"),
        paid_amount=data.get("paidAmount"),
        due_date=str(data.get("dueDate") or ""),
    )
