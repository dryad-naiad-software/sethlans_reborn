# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Custom authentication classes for the setup wizard.

DRF's stock :class:`rest_framework.authentication.SessionAuthentication`
only enforces CSRF when ``request.user.is_authenticated`` is true.  The
setup-phase session is **anonymous** -- ``setup_phase=True`` is merely a
session-dict flag; the Django ``User`` never logs in during the wizard.
As a result, mutating POSTs to ``/api/setup/*`` sail through DRF's built
in CSRF hook, violating spec FR-12 / F11.

:class:`SetupPhaseAuthentication` closes the gap: whenever it detects a
setup-phase session it calls ``self.enforce_csrf(request)`` explicitly
before returning the anonymous identity pair, guaranteeing that every
mutating request carries a valid CSRF token.  The bootstrap view opts
out by setting ``csrf_exempt = True`` on the view callable (the 256-bit
token itself is the anti-CSRF proof for that single call).
"""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from rest_framework.authentication import SessionAuthentication


SETUP_CSRF_EXEMPT_ATTR = "setup_phase_csrf_exempt"


def _is_setup_csrf_exempt(view) -> bool:
    """Return True if the resolved view opts out of setup-phase CSRF.

    We can't rely on Django's plain ``csrf_exempt`` attribute because
    DRF's ``@api_view`` decorator sets ``csrf_exempt = True`` on every
    function-based DRF view (so Django's own middleware skips the
    check and DRF can enforce it).  Views that want to opt out of our
    custom setup-phase CSRF enforcement must set a dedicated marker
    attribute (``setup_phase_csrf_exempt = True``) on the resolved
    view callable, its ``cls`` shim, or its ``view_class``.
    """
    if view is None:
        return False
    if getattr(view, SETUP_CSRF_EXEMPT_ATTR, False):
        return True
    cls = getattr(view, "cls", None)
    if cls is not None and getattr(cls, SETUP_CSRF_EXEMPT_ATTR, False):
        return True
    inner = getattr(view, "view_class", None)
    if inner is not None and getattr(inner, SETUP_CSRF_EXEMPT_ATTR, False):
        return True
    return False


class SetupPhaseAuthentication(SessionAuthentication):
    """Session auth that enforces CSRF for anonymous setup-phase sessions.

    Behavior:

    * If the request has ``session['setup_phase'] is True`` **and** the
      bound view is not CSRF-exempt, explicitly call
      :meth:`enforce_csrf` -- bypassing the built-in's
      ``user.is_authenticated`` gate -- and return
      ``(AnonymousUser(), None)`` on success.
    * If ``setup_phase`` is not set, fall back to :class:`SessionAuth
      entication`'s normal behaviour so authenticated admin sessions
      still work on any view that declares us.
    """

    def authenticate(self, request):
        session = getattr(request, "session", None)
        if session is not None and session.get("setup_phase") is True:
            # Honour per-view csrf_exempt opt-out (bootstrap view).  In
            # practice bootstrap declares ``authentication_classes=[]``
            # so this class never sees it; the check below is defence
            # in depth in case future refactors wire it through us.
            view_func = None
            inner = getattr(request, "_request", None)
            if inner is not None:
                resolver_match = getattr(inner, "resolver_match", None)
                if resolver_match is not None:
                    view_func = getattr(resolver_match, "func", None)

            if not _is_setup_csrf_exempt(view_func):
                # Explicit CSRF enforcement -- the stock check in
                # SessionAuthentication skips anonymous users.
                self.enforce_csrf(request)

            return (AnonymousUser(), None)

        # No setup-phase flag -- fall through to normal session auth so
        # admin-authenticated sessions continue to work.
        return super().authenticate(request)
