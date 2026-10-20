"""
Error reports to Sentry (docs/sentry.md). Off unless the server sets SENTRY_DSN, so local runs, CI and tests send
nothing. Errors in the web app and in the Celery worker are reported, tagged with the release (APP_VERSION) and the
environment (production or staging). No personal data is sent: no IP addresses, cookies, request bodies or user names
(send_default_pii=False), and Sentry's scrubber removes passwords, tokens and keys from what is left.

Requests for a host the site doesn't serve (bots scanning the server's IP address) are refused by Django as they should
be, and not reported: they are expected, many, and would bury real errors (DISALLOWED_HOST_LOGGER).
"""

DISALLOWED_HOST_LOGGER = "django.security.DisallowedHost"


def init(dsn, environment="production", release=None, traces_sample_rate=0.0):
    """Start Sentry when ``dsn`` is set; returns whether it started."""
    if not dsn:
        return False
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import ignore_logger

    sentry_sdk.init(
        dsn=dsn,
        environment=environment or "production",
        release=release or None,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        send_default_pii=False,          # no IPs, cookies, user names or request bodies
        traces_sample_rate=traces_sample_rate,   # 0: errors only, no performance tracing
    )
    ignore_logger(DISALLOWED_HOST_LOGGER)   # a wrong Host header is refused, never an error to look at
    return True
