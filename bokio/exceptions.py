class BokioError(Exception):
    """Base Bokio API error."""


class BokioConfigError(BokioError):
    """Missing or invalid BOKIO_TOKEN / BOKIO_COMPANY_ID."""


class BokioAuthError(BokioError):
    """401 from Bokio (revoked or invalid token)."""


class BokioNotFound(BokioError):
    """404 from Bokio."""


class BokioRateLimited(BokioError):
    """429 from Bokio after retry."""
