# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
URLconf-origin middleware — defense-in-depth for the loopback listener.

Part of the manager's waitress-migration (spec:
``development/specs/waitress-migration-manager.md`` Phases 2 & 5).

Phase 5 dual-Waitress topology:

* Public-origin Waitress listener on ``127.0.0.1:<public_port>`` —
  Caddy's public TLS vhost proxies here. Serves the full site via
  ``sethlans_manager.urls``.
* Internal-origin Waitress listener on ``127.0.0.1:<internal_port>`` —
  Caddy's loopback plaintext vhost proxies here. Serves only
  ``/api/status/public/`` via ``sethlans_manager.urls_loopback``.

Because Django's ``MIDDLEWARE`` stack is shared across every request
the WSGI application handles, this middleware runs once for every
request regardless of which listener accepted the socket. It reads
``request.META['SERVER_PORT']`` — which Waitress populates from the
accepting listener's local socket — and pins ``request.urlconf``
accordingly.

Header-injection protection: ``USE_X_FORWARDED_PORT`` and
``USE_X_FORWARDED_HOST`` are explicitly disabled in ``settings.py``.
Django therefore ignores any ``X-Forwarded-Port``/``X-Forwarded-Host``
header an attacker might inject and derives ``SERVER_PORT`` from the
underlying socket the request arrived on. Waitress is also started
with ``trusted_proxy`` unset (see
:mod:`sethlans_manager.waitress_launcher`), so no ``X-Forwarded-*``
header is honoured at the WSGI layer either. If any of these
invariants are reversed without removing this middleware, the
loopback/public split collapses — keep them off.

Fail-closed: if the request arrives on a port that is neither the
configured public nor the configured internal (loopback) port, return
HTTP 500 immediately. An unknown-port request indicates either a
misconfigured Caddyfile, a test harness that is not honouring the
invariants of the split-listener design, or a header-injection attempt
that slipped past ``USE_X_FORWARDED_PORT=False``. Phase 5 populates
both settings simultaneously; the ``WAITRESS_LOOPBACK_PORT_PUBLIC=None``
tolerance path is retained for unit tests that drive Django's test
client without pinning a public port.

Thumbnail-signal hazard (historical, resolved in Phase 4): prior
versions of ``signal_helpers.py`` used a ``post_save.disconnect``
/ ``connect`` dance that was not thread-safe. Phase 4 replaced that
with a per-thread ``_skip_thumbnail_signals()`` context manager, so
routing any thumbnail-triggering request through Waitress is now safe.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.http import HttpResponse

logger = logging.getLogger(__name__)

_LOOPBACK_URLCONF = "sethlans_manager.urls_loopback"


class UrlconfOriginMiddleware:
    """Pin ``request.urlconf`` based on the incoming server port.

    Eager init reads ``WAITRESS_LOOPBACK_PORT_PUBLIC`` and
    ``WAITRESS_LOOPBACK_PORT_INTERNAL`` from Django settings.  Both
    values are normalised to strings so they can be compared directly
    against ``request.META['SERVER_PORT']`` (which Django always stores
    as a string).
    """

    def __init__(self, get_response):
        self.get_response = get_response
        internal = getattr(settings, "WAITRESS_LOOPBACK_PORT_INTERNAL", None)
        public = getattr(settings, "WAITRESS_LOOPBACK_PORT_PUBLIC", None)
        self._internal_port = str(internal) if internal is not None else None
        self._public_port = str(public) if public is not None else None

    def __call__(self, request):
        server_port = str(request.META.get("SERVER_PORT", ""))

        if (
            self._internal_port is not None
            and server_port == self._internal_port
        ):
            # Loopback (Waitress) listener — pin the minimal URLconf.
            request.urlconf = _LOOPBACK_URLCONF
            return self.get_response(request)

        if self._public_port is None or server_port == self._public_port:
            # Main (uvicorn) listener — leave ROOT_URLCONF in effect.
            # When the public port is unset (e.g. early Phase 2 boot),
            # we treat any non-internal port as public.
            return self.get_response(request)

        # Unknown port — fail closed.  This indicates a misconfigured
        # forwarder, a test harness running on an unexpected port, or
        # a header-injection attempt that slipped past
        # USE_X_FORWARDED_PORT=False (which shouldn't be possible, but
        # we fail closed anyway).
        logger.error(
            "UrlconfOriginMiddleware: unknown SERVER_PORT=%r "
            "(public=%r internal=%r) — refusing request.",
            server_port, self._public_port, self._internal_port,
        )
        return HttpResponse(
            "Internal Server Error: unknown listener port.",
            status=500,
        )
