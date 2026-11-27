# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``run_manager.py --setup`` with the worker program replaced by
``_fake_worker_cli.py`` (setup page browser tests, setup-rebuild chunk 5).

Setup mode finds the worker program through
``sethlans_manager.setup_mode.worker_cli.worker_command``; this starter
replaces that one function in the child process and then runs
``manager/run_manager.py`` unchanged. The fake's folder comes in
``SETUP_TEST_FAKE_WORKER_DIR`` (removed before the manager code runs).
"""
import os
import runpy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_MANAGER = REPO_ROOT / 'manager' / 'run_manager.py'
FAKE = Path(__file__).with_name('_fake_worker_cli.py')
FOLDER_ENV = 'SETUP_TEST_FAKE_WORKER_DIR'


def main() -> None:
    folder = os.environ.pop(FOLDER_ENV)
    for path in (str(REPO_ROOT / 'manager'), str(REPO_ROOT)):
        if path not in sys.path:
            sys.path.insert(0, path)
    from sethlans_manager.setup_mode import worker_cli

    worker_cli.worker_command = lambda: [sys.executable, str(FAKE), folder]
    sys.argv = [str(RUN_MANAGER)] + sys.argv[1:]
    runpy.run_path(str(RUN_MANAGER), run_name='__main__')


if __name__ == '__main__':
    main()
