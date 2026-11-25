# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup mode: ``run_manager --setup`` serves the first-run setup page and
writes ``sethlans.ini``, then exits 10 (``entry``). It has no run-time
database and never loads the ``workers`` app (``settings_setup``).

Also a Django app (``INSTALLED_APPS`` of ``settings_setup``), so this
module imports nothing at load time.
"""


def main(argv=None) -> int:
    """Run setup mode or ``--hash-password``; return the exit code."""
    import sys

    from sethlans_manager.setup_mode.entry import run
    return run(sys.argv[1:] if argv is None else list(argv))
