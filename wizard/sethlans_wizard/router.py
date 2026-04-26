# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tiny path router for the wizard's WSGI app.

Extracted from :mod:`wizard.sethlans_wizard.server` in B2 to keep that
module under the project's 300-line ceiling once the static-file mount
points were added (FR-W-FE2). Behaviour is unchanged from the original
inline ``Router`` class except for the added ``exact`` flag, which lets
the static-file routes register as prefix mounts while the API
endpoints keep exact-equal match semantics.
"""

from __future__ import annotations

from typing import Callable, Iterable


class Router:
    """Tiny path dispatcher used by :func:`server.create_app`.

    Routes are evaluated in registration order; the first match wins.
    Default registration is exact-equal match (``add(prefix, handler)``)
    to preserve the API endpoints' original semantics. Pass
    ``exact=False`` to register a prefix mount used by the static-file
    handlers.

    Kept deliberately simple — the wizard only ever exposes a small
    handful of endpoints (FR-W7, FR-W8, FR-W9, FR-W14 plus static
    pages from FR-W-FE2). No regex, no method dispatch table.
    """

    def __init__(self) -> None:
        # Each route is (prefix, handler, exact).
        self._routes: list[tuple[str, Callable, bool]] = []

    def add(self, prefix: str, handler: Callable, exact: bool = True) -> None:
        """Register WSGI *handler* for path *prefix*.

        Defaults to exact-equal match (the wizard API endpoints all rely
        on this). Pass ``exact=False`` to register a prefix mount (used
        by the static-file routes for ``/static/...``).
        """
        if not prefix.startswith("/"):
            raise ValueError(f"route prefix must start with '/': {prefix!r}")
        self._routes.append((prefix, handler, exact))

    def dispatch(
        self,
        environ: dict,
        start_response: Callable,
    ) -> Iterable[bytes]:
        path = environ.get("PATH_INFO", "") or ""
        for prefix, handler, exact in self._routes:
            if exact:
                if path == prefix:
                    return handler(environ, start_response)
            else:
                if path.startswith(prefix):
                    return handler(environ, start_response)
        return _send_404(start_response)


def _send_404(start_response: Callable) -> Iterable[bytes]:
    body = b'{"error": "Not Found"}'
    start_response(
        "404 Not Found",
        [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]


__all__ = ["Router"]
