# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared Caddy subprocess supervision (manager + worker).

Originally introduced for the worker agent in worker spec Phase 5b,
then extracted to ``shared/`` in manager spec Phase 3 so both the
Django manager launcher and the worker agent can supervise a Caddy
child process without code duplication.

The supervisor is generic: it accepts a pure-function Caddyfile
renderer plus template kwargs, the env-var name that activates the
external (Docker) Caddyfile branch, and a mapping used to overlay
those kwargs onto Caddy's spawn env in the external branch. Worker-
and manager-specific templates live alongside their own code
(``worker/sethlans_worker_agent/caddy_template.py`` and
``manager/sethlans_manager/caddy_template.py`` respectively).

Supervision semantics mirror the canonical lifecycle table in
``development/specs/waitress-migration-manager.md`` (see the
"Caddy crashes mid-operation" row):

* Max 3 restart attempts with 1 s backoff between attempts.
* Restart counter resets after 60 s of stable uptime.
* After 3 failed restarts, ``error_event`` is set and the supervisor
  stops attempting. The caller polls the flag and exits 1 —
  ``caddy_supervisor`` never unilaterally kills the caller.

Subprocess hardening:

* List-form ``subprocess.Popen([...])`` — never ``shell=True``.
* Binary path validated (``is_file`` + executable bit on POSIX)
  before spawn.
* POSIX: ``preexec_fn=os.setsid`` puts Caddy in its own process group
  so ``os.killpg`` can deliver a group-wide SIGTERM at shutdown. On
  Linux, ``PR_SET_PDEATHSIG(SIGTERM)`` is additionally set via
  ``ctypes.prctl`` so Caddy is killed if the parent is SIGKILLed.
* Windows: ``CREATE_NEW_PROCESS_GROUP`` allows ``CTRL_BREAK_EVENT`` to
  be delivered to Caddy. A Windows Job Object would additionally
  guarantee orphan-kill on parent death but requires ``pywin32``,
  not part of this package's dependency surface.
"""

from shared.caddy_supervisor.supervisor import (
    CaddyBinaryNotFoundError,
    CaddySupervisor,
    CaddyfileNotFoundError,
    MAX_RESTART_ATTEMPTS,
    RESTART_BACKOFF_SECONDS,
    SHUTDOWN_DRAIN_DEFAULT_SECONDS,
    STABLE_UPTIME_RESET_SECONDS,
    WATCHDOG_POLL_SECONDS,
)
from shared.caddy_supervisor.io import atomic_write_text

__all__ = [
    "CaddyBinaryNotFoundError",
    "CaddySupervisor",
    "CaddyfileNotFoundError",
    "MAX_RESTART_ATTEMPTS",
    "RESTART_BACKOFF_SECONDS",
    "SHUTDOWN_DRAIN_DEFAULT_SECONDS",
    "STABLE_UPTIME_RESET_SECONDS",
    "WATCHDOG_POLL_SECONDS",
    "atomic_write_text",
]
