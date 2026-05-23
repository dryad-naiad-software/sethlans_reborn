# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Post-health liveness check for the install smoke (issue #203).

Extracted from ``tools/_install_smoke_driver.py`` per FR-SMOKE5 — the
driver was already at the 300-line ceiling, so the regression check
that catches #203 lives here.

The 15-second sleep is intentional and load-bearing — see
:func:`check_post_health_liveness` for the rationale.
"""

from __future__ import annotations

import ssl
import subprocess
import time
import urllib.error
import urllib.request

from _wizard_smoke_helpers import err, http_get_ok

# Issue #204: per-call hang guard. The deadlocked endpoint never returns
# a response, so a *timeout* (not a status) is the signal. Keep this
# tight — 5 s is well below normal latency for any 4xx response path.
AUTH_USER_TIMEOUT_SECONDS = 5


def check_post_health_liveness(
    manager_port: int,
    launcher_proc: subprocess.Popen,
) -> bool:
    """Assert launcher + Caddy still alive 15 s after health (issue #203).

    The 15-second sleep is intentional: the observed launcher-exit
    latency from issue #203 was ~10 seconds post-handoff (the
    ``supervision.shutdown_supervisors()`` finally-block fired moments
    after ``hand_off_to_runtime`` returned, but the launcher process
    itself stayed alive for ~10 s while the diagnostics flush + lock
    release ran). Sleeping below 10 s risks a false pass on the same
    regression class. Do NOT lower this threshold without first
    confirming the #203 root cause is closed.
    """
    print("liveness: sleeping 15s before re-check (issue #203)...")
    time.sleep(15)
    url = f"https://127.0.0.1:{manager_port}/api/health/"
    if not http_get_ok(url):
        err(
            "liveness FAILED: manager health probe failed 15s "
            "after initial success"
        )
        return False
    if launcher_proc.poll() is not None:
        err(
            f"liveness FAILED: launcher exited (code "
            f"{launcher_proc.poll()}) 15s after initial health success"
        )
        return False
    try:
        import psutil
        launcher_ps = psutil.Process(launcher_proc.pid)
        descendants = launcher_ps.children(recursive=True)
        caddy_alive = any(
            "caddy" in (p.name() or "").lower() for p in descendants
        )
        if not caddy_alive:
            err(
                "liveness FAILED: caddy process not found among "
                "launcher descendants 15s after initial health success"
            )
            return False
    except ImportError:
        print("liveness: psutil unavailable; skipping Caddy descendant check")
    except Exception as exc:  # noqa: BLE001 — psutil quirks vary by OS
        print(f"liveness: psutil check skipped ({exc})")
    if not _check_auth_user_endpoint(manager_port):
        return False
    print("liveness OK: launcher alive, manager healthy, Caddy running")
    return True


def _check_auth_user_endpoint(manager_port: int) -> bool:
    """Probe a 4xx-returning endpoint to catch the issue #204 deadlock.

    The hang in #204 was rooted in the manager subprocess's logging
    handler RLock — the lock was held while a blocked ``write()`` to a
    full pipe waited forever. Once that state was reached, *any*
    endpoint whose response path emitted a log record (notably Waitress's
    WARN-level log for 4xx responses) wedged the worker thread that
    served it. ``/api/auth/user/`` returns 401/403 for an unauthenticated
    caller, so it both reproduces the hang class and short-circuits
    quickly under healthy conditions.

    Caveat: the pipe takes minutes to fill in production, so a single
    probe right after the 15 s liveness sleep will not reliably trip
    the bug. The value here is the *contract* — a future aged run will
    expose any regression in the launcher's stdio routing.
    """
    url = f"https://127.0.0.1:{manager_port}/api/auth/user/"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    started = time.monotonic()
    try:
        with urllib.request.urlopen(
            url, timeout=AUTH_USER_TIMEOUT_SECONDS, context=ctx,
        ) as resp:
            elapsed = time.monotonic() - started
            print(
                f"auth-user probe OK: {resp.status} in {elapsed:.2f}s "
                f"(<{AUTH_USER_TIMEOUT_SECONDS}s, no hang)"
            )
            return True
    except urllib.error.HTTPError as exc:
        # 401/403/404 — *any* response means the worker thread did not
        # wedge. Issue #204 manifests as a timeout, never as an HTTPError.
        elapsed = time.monotonic() - started
        print(
            f"auth-user probe OK: HTTP {exc.code} in {elapsed:.2f}s "
            f"(<{AUTH_USER_TIMEOUT_SECONDS}s, no hang)"
        )
        return True
    except (urllib.error.URLError, OSError) as exc:
        elapsed = time.monotonic() - started
        err(
            f"auth-user FAILED: {url} did not respond within "
            f"{AUTH_USER_TIMEOUT_SECONDS}s "
            f"(elapsed {elapsed:.2f}s, exc={exc!r}). This matches the "
            f"issue #204 deadlock signature — verify _start_component "
            f"is NOT using subprocess.PIPE for manager/worker."
        )
        return False


__all__ = ["check_post_health_liveness"]
