# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frozen-mode Django management-command dispatcher for ``run_manager``.

Issue #191: in frozen PyInstaller mode the launcher cannot invoke
``[sys.executable, manage.py, ...]`` because ``sys.executable`` resolves
to ``run_launcher.exe`` (which has its own argparse and rejects unknown
args). The fix teaches ``run_manager.exe`` a ``--manage <subcommand>``
mode and routes the launcher's apply pipeline through it.

The dispatcher lives in this module rather than ``run_manager.py``
itself to keep both files under the project-wide 300-line cap.

Key contract (see ``development/specs/frozen_apply_pipeline.md``):

* ``_ALLOWED_MANAGE_SUBCOMMANDS`` is an allowlist enforced BEFORE any
  Django initialization, so disallowed commands cannot leverage
  ``django.setup`` side-effects.
* ``setup_django_for_management()`` performs ONLY ``django.setup()`` +
  logging configuration; it never runs migrations (those travel via
  ``--manage migrate`` from the launcher).
* ``dispatch_manage_mode()`` calls ``execute_from_command_line`` WITHOUT
  a ``try/except SystemExit`` wrapper: the Django command terminates
  the process itself on non-zero exit, and we fall through to
  ``sys.exit(0)`` only on the success path.
"""

from __future__ import annotations

import sys
from typing import Iterable

# Subcommands the launcher is permitted to invoke against the frozen
# manager binary. Adding entries here that accept password / token /
# API-key args on argv MUST also add the corresponding redaction in
# launcher/apply_pending_setup.py before the argv reaches the logger
# (see spec FR-LAUNCHER3 + FR-MGMT3 trailing paragraph).
_ALLOWED_MANAGE_SUBCOMMANDS = {"migrate", "apply_pending_setup"}


def setup_django_for_management() -> None:
    """Initialize Django + logging for a one-shot management command.

    Identical preamble to ``_frozen_boot_sequence`` but WITHOUT any
    migration / collectstatic side-effects. Migrations are owned by the
    caller's argv (``--manage migrate``) so this helper must remain
    pure setup.

    If a future edit installs a module-level ``django.setup()`` in
    ``run_manager.py``, the ``apps.ready`` check below skips the
    second call to avoid the "Apps aren't loaded yet" sequencing trap.
    """
    import django
    from django.apps import apps

    if not apps.ready:
        try:
            django.setup()
        except Exception as exc:
            print(
                f"\n[ERROR] Django setup failed: {exc}", file=sys.stderr,
            )
            sys.exit(1)

        # _cfg() reads from Django settings and registers handlers tied to
        # the app registry, so it must only run once per process — paired
        # with the django.setup() call that populated that registry. If
        # apps.ready was already True on entry, another caller in this
        # process owns the logging config already; skip the second pass.
        from sethlans_manager.logging_config import configure as _cfg
        _cfg()


def _argv_after_manage(argv: Iterable[str]) -> list[str]:
    """Return the slice of argv that follows ``--manage``.

    Returns an empty list if ``--manage`` is the last token.
    """
    argv_list = list(argv)
    idx = argv_list.index("--manage")
    return argv_list[idx + 1:]


def dispatch_manage_mode(argv: list[str] | None = None) -> None:
    """Validate argv, enforce the allowlist, dispatch to Django.

    On success this function calls ``sys.exit(0)``. On failure it
    either:
      * exits 2 (argv / allowlist failure) BEFORE Django init, or
      * lets ``execute_from_command_line`` terminate the process itself
        with whatever exit code Django decides.

    There is no ``try/except SystemExit`` wrapper around the dispatch
    call. The Django command either returns ``None`` on success (we
    fall through to ``sys.exit(0)``) or terminates the process via
    ``SystemExit`` / ``os._exit``.
    """
    if argv is None:
        argv = sys.argv

    argv_list = list(argv)

    # Reject duplicate --manage up front so the index() lookup below can't
    # silently parse the first occurrence while a second slips through as
    # a subcommand argument and bypasses the allowlist intent.
    if argv_list.count("--manage") > 1:
        print(
            "--manage may not be specified more than once", file=sys.stderr,
        )
        sys.exit(2)

    # Only treat --dev as a top-level flag if it appears BEFORE --manage.
    # Anything after --manage is the subcommand + its args; a literal
    # "--dev" arg value there (e.g. a perverse --data-dir value) must not
    # false-positive the mutex.
    if "--manage" in argv_list:
        manage_idx = argv_list.index("--manage")
        top_level = argv_list[:manage_idx]
        if "--dev" in top_level:
            print(
                "--manage and --dev are mutually exclusive", file=sys.stderr,
            )
            sys.exit(2)

    post = _argv_after_manage(argv_list)
    if not post:
        print(
            "--manage requires a Django management command name",
            file=sys.stderr,
        )
        sys.exit(2)

    subcommand = post[0]
    sub_args = post[1:]

    if subcommand not in _ALLOWED_MANAGE_SUBCOMMANDS:
        print(
            f"manage subcommand '{subcommand}' not in allowlist",
            file=sys.stderr,
        )
        sys.exit(2)

    setup_django_for_management()

    from django.core.management import execute_from_command_line
    # argv[0] is conventionally the program name; Django's own examples
    # use "manage". Don't substitute sys.argv[0] here — it would be the
    # frozen exe path and break Django's usage messages.
    execute_from_command_line(["manage", subcommand, *sub_args])
    # If we reach this point, the management command returned None
    # (success). Any non-zero exit was raised via SystemExit / os._exit
    # inside Django and never returned control to us.
    sys.exit(0)


__all__ = [
    "setup_django_for_management",
    "dispatch_manage_mode",
]
