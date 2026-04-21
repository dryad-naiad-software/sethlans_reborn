# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Caddy subprocess supervision for the worker agent (Phase 5b).

Templates the worker Caddyfile via
:func:`sethlans_worker_agent.caddy_template.render_worker_caddyfile`,
writes it atomically to ``<worker_data_dir>/caddy/Caddyfile``, spawns
Caddy as a supervised child process, and runs a background watchdog
that restarts Caddy on crash.

Supervision semantics mirror the canonical lifecycle table in
``development/specs/waitress-migration-manager.md`` (see the
"Caddy crashes mid-operation" row):

* Max 3 restart attempts with 1 s backoff between attempts.
* Restart counter resets after 60 s of stable uptime.
* After 3 failed restarts, ``error_event`` is set and the supervisor
  stops attempting. ``agent.py`` polls the flag and exits 1 —
  ``caddy_supervisor`` never unilaterally kills the worker process.

Subprocess hardening:

* List-form ``subprocess.Popen([...])`` — never ``shell=True``.
* Binary path resolved via :func:`shared.frozen_paths.get_caddy_path`;
  existence is asserted before spawn.
* POSIX: ``preexec_fn=os.setsid`` puts Caddy in its own process group
  so ``os.killpg`` can deliver a group-wide SIGTERM at shutdown. On
  Linux, ``PR_SET_PDEATHSIG(SIGTERM)`` is additionally set via
  ``ctypes.prctl`` so Caddy is killed if the worker agent is SIGKILLed.
* Windows: ``CREATE_NEW_PROCESS_GROUP`` allows ``CTRL_BREAK_EVENT`` to
  be delivered to Caddy. A Windows Job Object would additionally
  guarantee orphan-kill on parent death but requires ``pywin32``,
  which is not a worker runtime dependency; that gap is documented
  and left to installer-level supervision.
"""

from sethlans_worker_agent.caddy_supervisor.supervisor import (
    CaddyBinaryNotFoundError,
    CaddySupervisor,
    MAX_RESTART_ATTEMPTS,
    RESTART_BACKOFF_SECONDS,
    SHUTDOWN_DRAIN_DEFAULT_SECONDS,
    STABLE_UPTIME_RESET_SECONDS,
    WATCHDOG_POLL_SECONDS,
)
from sethlans_worker_agent.caddy_supervisor.io import atomic_write_text

__all__ = [
    "CaddyBinaryNotFoundError",
    "CaddySupervisor",
    "MAX_RESTART_ATTEMPTS",
    "RESTART_BACKOFF_SECONDS",
    "SHUTDOWN_DRAIN_DEFAULT_SECONDS",
    "STABLE_UPTIME_RESET_SECONDS",
    "WATCHDOG_POLL_SECONDS",
    "atomic_write_text",
]
