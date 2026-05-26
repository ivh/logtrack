import logging
import time
from functools import lru_cache
from typing import Any

import requests
from django.conf import settings

from .exceptions import (
    BokioAuthError,
    BokioConfigError,
    BokioError,
    BokioNotFound,
    BokioRateLimited,
)

logger = logging.getLogger("bokio")


class BokioClient:
    def __init__(self, token: str, company_id: str, base_url: str):
        if not token or not company_id:
            raise BokioConfigError("BOKIO_TOKEN och BOKIO_COMPANY_ID måste vara satta")
        self.token = token
        self.company_id = company_id
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _url(self, path: str) -> str:
        return f"{self.base_url}/companies/{self.company_id}{path}"

    def _request(self, method: str, path: str, *, json: dict | None = None) -> dict:
        url = self._url(path)
        logger.debug("bokio %s %s", method, url)
        for attempt in (1, 2):
            resp = self.session.request(method, url, json=json, timeout=15)
            if resp.status_code == 429 and attempt == 1:
                retry_after = int(resp.headers.get("Bokio-RateLimit-RetryAfter", "1"))
                logger.warning("bokio 429, sleeping %ss", retry_after)
                time.sleep(retry_after)
                continue
            break
        return self._handle(resp)

    @staticmethod
    def _handle(resp: requests.Response) -> dict:
        if resp.status_code == 401:
            raise BokioAuthError("ogiltig eller utgången Bokio-token")
        if resp.status_code == 404:
            raise BokioNotFound(f"Bokio: 404 {resp.url}")
        if resp.status_code == 429:
            raise BokioRateLimited("Bokio: 429 efter retry")
        if not resp.ok:
            raise BokioError(f"Bokio {resp.status_code}: {resp.text[:200]}")
        if not resp.content:
            return {}
        return resp.json()

    def add_line_item(self, invoice_id: str, line: dict[str, Any]) -> dict:
        return self._request("POST", f"/invoices/{invoice_id}/line-items", json=line)

    def create_draft_invoice(self, payload: dict[str, Any]) -> dict:
        return self._request("POST", "/invoices", json=payload)


@lru_cache(maxsize=1)
def get_client() -> BokioClient:
    return BokioClient(
        token=settings.BOKIO_TOKEN,
        company_id=settings.BOKIO_COMPANY_ID,
        base_url=settings.BOKIO_API_BASE,
    )
