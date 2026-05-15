"""Single source of truth for environment-derived URLs.

`ENVIRONMENT` ∈ {`local`, `dev`, `production`} drives FRONTEND_URL,
API_URL, CORS_ORIGINS, and the admin-route gate. Individual values
can still be overridden by the matching env var if needed.
"""

import os


ENVIRONMENT = os.environ.get("ENVIRONMENT", "local").lower()

_URLS: dict[str, dict[str, str]] = {
    "local":      {"frontend": "http://localhost:4200",       "api": "http://localhost:4100"},
    "dev":        {"frontend": "https://dev.sailratings.com", "api": "https://api.dev.sailratings.com"},
    "production": {"frontend": "https://sailratings.com",     "api": "https://api.sailratings.com"},
}

if ENVIRONMENT not in _URLS:
    raise RuntimeError(
        f"Unknown ENVIRONMENT={ENVIRONMENT!r}; expected one of {sorted(_URLS)}"
    )

FRONTEND_URL: str = os.environ.get("FRONTEND_BASE_URL") or _URLS[ENVIRONMENT]["frontend"]
API_URL: str = os.environ.get("API_BASE_URL") or _URLS[ENVIRONMENT]["api"]

_default_cors: dict[str, list[str]] = {
    "local":      [FRONTEND_URL, "http://localhost:3000"],
    "dev":        [FRONTEND_URL],
    "production": [FRONTEND_URL, "https://www.sailratings.com"],
}

CORS_ORIGINS: list[str] = (
    [o.strip() for o in os.environ["CORS_ORIGINS"].split(",") if o.strip()]
    if os.environ.get("CORS_ORIGINS")
    else _default_cors[ENVIRONMENT]
)

IS_PRODUCTION: bool = ENVIRONMENT == "production"
