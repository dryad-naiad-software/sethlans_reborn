# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Route registration helper for the wizard WSGI app.

Extracted from :mod:`wizard.sethlans_wizard.server` in Phase 1 (Spec 2)
once the new step handlers (network, database, admin-user,
worker-password, ffmpeg start/cancel/progress, verify, pending-setup)
pushed ``server.py`` over the project's 300-line ceiling. Behaviour
is unchanged — every route the previous monolithic ``create_app`` wired
is still wired here, in the same registration order.

Order matters (first match wins): API routes register first as exact
matches; the FFmpeg progress route is the only API-side prefix mount
(its tail path component is the ``task_id``). Static-file mounts are
registered last so they can never shadow an API endpoint.
"""

from __future__ import annotations

from pathlib import Path

from wizard.sethlans_wizard.handlers.admin_user import (
    make_admin_user_handler,
)
from wizard.sethlans_wizard.handlers.auth import make_auth_handler
from wizard.sethlans_wizard.handlers.database import make_database_handler
from wizard.sethlans_wizard.handlers.done import make_done_handler
from wizard.sethlans_wizard.handlers.ffmpeg import (
    make_cancel_handler as make_ffmpeg_cancel_handler,
    make_progress_handler as make_ffmpeg_progress_handler,
    make_start_handler as make_ffmpeg_start_handler,
)
from wizard.sethlans_wizard.handlers.health import make_health_handler
from wizard.sethlans_wizard.handlers.launcher_log_path import (
    make_launcher_log_path_handler,
)
from wizard.sethlans_wizard.handlers.network import make_network_handler
from wizard.sethlans_wizard.handlers.pending_setup import (
    make_pending_setup_handler,
)
from wizard.sethlans_wizard.handlers.resume_target import (
    make_resume_target_handler,
)
from wizard.sethlans_wizard.handlers.runtime_ready import (
    make_runtime_ready_handler,
)
from wizard.sethlans_wizard.handlers.static_files import (
    make_index_handler,
    make_index_handler_authed,
    make_static_handler,
)
from wizard.sethlans_wizard.handlers.topology import make_topology_handler
from wizard.sethlans_wizard.handlers.verify import make_verify_handler
from wizard.sethlans_wizard.handlers.welcome import make_welcome_handler
from wizard.sethlans_wizard.handlers.worker_password import (
    make_worker_password_handler,
)
from wizard.sethlans_wizard.router import Router


def register_routes(
    router: Router,
    *,
    data_dir: Path,
    setup_token: bytes,
    ipc_secret: bytes,
    wizard_port: int,
    static_root: Path,
) -> None:
    """Register every wizard route on *router*. Order matters."""
    # FR-W14 anonymous cold-boot probe — first so static can't shadow.
    router.add("/api/health/", make_health_handler())

    # Spec 1 endpoints.
    router.add("/api/wizard/auth/", make_auth_handler(setup_token))
    router.add("/api/wizard/topology/", make_topology_handler(data_dir))
    router.add(
        "/api/wizard/done/",
        make_done_handler(data_dir, ipc_secret, wizard_port=wizard_port),
    )
    router.add(
        "/api/wizard/runtime-ready/",
        make_runtime_ready_handler(data_dir, ipc_secret),
    )
    router.add(
        "/api/wizard/launcher-log-path/",
        make_launcher_log_path_handler(data_dir),
    )

    # Phase 1 (Spec 2) — new step handlers.
    router.add("/api/wizard/network/", make_network_handler(data_dir))
    router.add("/api/wizard/database/", make_database_handler(data_dir))
    router.add("/api/wizard/admin-user/", make_admin_user_handler(data_dir))
    router.add(
        "/api/wizard/worker-password/",
        make_worker_password_handler(data_dir),
    )
    router.add(
        "/api/wizard/ffmpeg/start/",
        make_ffmpeg_start_handler(data_dir),
    )
    router.add("/api/wizard/ffmpeg/cancel/", make_ffmpeg_cancel_handler())
    # Prefix mount — task_id is a path component. Registered AFTER the
    # exact start/cancel routes so first-match-wins picks them.
    router.add(
        "/api/wizard/ffmpeg/progress/",
        make_ffmpeg_progress_handler(),
        exact=False,
    )
    router.add("/api/wizard/verify/", make_verify_handler(data_dir))
    router.add(
        "/api/wizard/pending-setup/",
        make_pending_setup_handler(data_dir),
    )
    # Phase 2 (Spec 2) — welcome + resume-target endpoints.
    router.add("/api/wizard/welcome/", make_welcome_handler(data_dir))
    router.add(
        "/api/wizard/resume-target/",
        make_resume_target_handler(data_dir),
    )

    # Frontend pages + vendored assets (FR-W-FE2).
    for subdir in ("vendor", "css", "js"):
        prefix = f"/static/{subdir}/"
        router.add(
            prefix,
            make_static_handler(static_root / subdir, prefix),
            exact=False,
        )
    # FR-M2-1: GET / serves welcome.html; the legacy token-entry page
    # moves to GET /token.
    #
    # Issue #175 — every wizard page route EXCEPT ``/token`` and
    # ``/redirecting`` is gated by the ``wizard_session`` cookie via
    # ``make_index_handler_authed``. Unauthed GETs 302 to /token; the
    # token-entry page itself stays unauthed (entry point) and the
    # post-handoff redirecting page stays unauthed (the wizard is
    # already shutting down — gating here would just race the cookie
    # invalidation in clear_session_token()).
    router.add("/", make_index_handler_authed(static_root, "welcome.html"))
    router.add("/token", make_index_handler(static_root, "index.html"))
    router.add(
        "/topology",
        make_index_handler_authed(static_root, "topology.html"),
    )
    router.add(
        "/network",
        make_index_handler_authed(static_root, "network.html"),
    )
    router.add(
        "/database",
        make_index_handler_authed(static_root, "database.html"),
    )
    router.add(
        "/admin-user",
        make_index_handler_authed(static_root, "admin-user.html"),
    )
    router.add(
        "/worker-password",
        make_index_handler_authed(static_root, "worker-password.html"),
    )
    router.add(
        "/ffmpeg",
        make_index_handler_authed(static_root, "ffmpeg.html"),
    )
    router.add(
        "/verify",
        make_index_handler_authed(static_root, "verify.html"),
    )
    router.add(
        "/done",
        make_index_handler_authed(static_root, "done.html"),
    )
    router.add(
        "/redirecting",
        make_index_handler(static_root, "redirecting.html"),
    )


__all__ = ["register_routes"]
