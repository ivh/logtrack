import pytest
from django.test import override_settings

from bokio.client import get_client


@pytest.fixture(autouse=True)
def _no_live_bokio():
    """Keep the test suite hermetic: with a real .env present, BOKIO_TOKEN is
    set, so any unmocked get_client() would hit the live Bokio API. Force empty
    credentials so unmocked calls raise BokioConfigError instead of going out
    on the network. Tests that patch get_client are unaffected.
    """
    get_client.cache_clear()
    with override_settings(BOKIO_TOKEN="", BOKIO_COMPANY_ID=""):
        yield
    get_client.cache_clear()
