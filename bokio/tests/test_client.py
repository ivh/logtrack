import pytest
import responses
from django.test import override_settings

from bokio.client import BokioClient
from bokio.exceptions import (
    BokioAuthError,
    BokioConfigError,
    BokioError,
    BokioNotFound,
    BokioRateLimited,
)

API = "https://api.bokio.se/v1"
COMPANY = "company-uuid"
LINE_URL = f"{API}/companies/{COMPANY}/invoices/inv-1/line-items"


def _client():
    return BokioClient(token="tok", company_id=COMPANY, base_url=API)


def test_missing_credentials_raises_config_error():
    with pytest.raises(BokioConfigError):
        BokioClient(token="", company_id=COMPANY, base_url=API)
    with pytest.raises(BokioConfigError):
        BokioClient(token="tok", company_id="", base_url=API)


@responses.activate
def test_add_line_item_sends_bearer_and_returns_body():
    responses.add(responses.POST, LINE_URL, json={"id": "li-1"}, status=200)
    result = _client().add_line_item("inv-1", {"description": "x", "quantity": 1, "unitPrice": "10"})
    assert result == {"id": "li-1"}
    assert responses.calls[0].request.headers["Authorization"] == "Bearer tok"


@responses.activate
def test_429_with_retry_after_then_success():
    responses.add(
        responses.POST,
        LINE_URL,
        status=429,
        headers={"Bokio-RateLimit-RetryAfter": "0"},
    )
    responses.add(responses.POST, LINE_URL, json={"id": "li-2"}, status=200)
    result = _client().add_line_item("inv-1", {})
    assert result == {"id": "li-2"}
    assert len(responses.calls) == 2


@responses.activate
def test_429_twice_raises_rate_limited():
    responses.add(responses.POST, LINE_URL, status=429, headers={"Bokio-RateLimit-RetryAfter": "0"})
    responses.add(responses.POST, LINE_URL, status=429, headers={"Bokio-RateLimit-RetryAfter": "0"})
    with pytest.raises(BokioRateLimited):
        _client().add_line_item("inv-1", {})


@responses.activate
def test_401_raises_auth_error():
    responses.add(responses.POST, LINE_URL, status=401)
    with pytest.raises(BokioAuthError):
        _client().add_line_item("inv-1", {})


@responses.activate
def test_404_raises_not_found():
    responses.add(responses.POST, LINE_URL, status=404)
    with pytest.raises(BokioNotFound):
        _client().add_line_item("inv-1", {})


@responses.activate
def test_other_error_raises_base_error():
    responses.add(responses.POST, LINE_URL, status=500, body="boom")
    with pytest.raises(BokioError):
        _client().add_line_item("inv-1", {})


@override_settings(BOKIO_TOKEN="", BOKIO_COMPANY_ID="")
def test_get_client_with_empty_settings_raises():
    from bokio.client import get_client
    get_client.cache_clear()
    with pytest.raises(BokioConfigError):
        get_client()
    get_client.cache_clear()


@responses.activate
def test_create_draft_invoice_posts_to_invoices():
    url = f"{API}/companies/{COMPANY}/invoices"
    responses.add(responses.POST, url, json={"id": "inv-new", "lineItems": [{"id": "li-1"}]}, status=200)
    result = _client().create_draft_invoice({"invoiceDate": "2026-05-26", "lineItems": []})
    assert result["id"] == "inv-new"
    assert responses.calls[0].request.headers["Authorization"] == "Bearer tok"


@responses.activate
def test_get_invoice_returns_body():
    url = f"{API}/companies/{COMPANY}/invoices/inv-1"
    responses.add(
        responses.GET,
        url,
        json={"id": "inv-1", "status": "published", "customerRef": {"name": "Kund AB"}},
        status=200,
    )
    result = _client().get_invoice("inv-1")
    assert result["status"] == "published"
    assert result["customerRef"]["name"] == "Kund AB"
    assert responses.calls[0].request.method == "GET"
    assert responses.calls[0].request.headers["Authorization"] == "Bearer tok"


@responses.activate
def test_list_invoices_sends_status_filter():
    url = f"{API}/companies/{COMPANY}/invoices"
    responses.add(
        responses.GET,
        url,
        json={"items": [{"id": "inv-1", "status": "draft"}], "totalItems": 1},
        status=200,
    )
    result = _client().list_invoices(status="draft", page_size=50)
    assert result["items"][0]["id"] == "inv-1"
    sent = responses.calls[0].request
    assert "query=status%3D%3Ddraft" in sent.url
    assert "pageSize=50" in sent.url


@responses.activate
def test_get_invoice_404_raises_not_found():
    url = f"{API}/companies/{COMPANY}/invoices/gone"
    responses.add(responses.GET, url, status=404)
    with pytest.raises(BokioNotFound):
        _client().get_invoice("gone")


def test_url_includes_v1_prefix_and_company_id():
    c = _client()
    assert c._url("/invoices") == f"{API}/companies/{COMPANY}/invoices"
    # ensure tests would catch a regression to a sunset (pre-v1) URL
    assert "/v1/" in c._url("/invoices")
