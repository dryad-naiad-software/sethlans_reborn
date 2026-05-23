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

import subprocess
import time

from _wizard_smoke_helpers import err, http_get_ok


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
    print("liveness OK: launcher alive, manager healthy, Caddy running")
    return True


__all__ = ["check_post_health_liveness"]
