# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The setup-process teardown ends the whole process tree it started (#343).

Real parts: ``setup_process.end_tree`` with a real child Python process that
starts its own child, as ``run_manager --setup`` starts the worker program.
"""
import os
import subprocess
import sys

import psutil
import pytest

from .setup_process import end_tree

pytestmark = pytest.mark.timeout(60, method="thread")

WAIT_S = 20.0
# The child starts a grandchild that would outlive it by far, prints its pid, then waits as well.
CHILD = (
    "import subprocess, sys, time\n"
    "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'],"
    " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)\n"
    "print(grandchild.pid, flush=True)\n"
    "time.sleep(600)\n"
)


def test_end_tree_kills_the_child_and_the_process_it_started():
    """#343: the teardown killed only the setup child, so a worker command it was running lived on;
    ``end_tree`` ends the child and that grandchild."""
    group = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'process_group': 0}
    proc = subprocess.Popen([sys.executable, '-c', CHILD], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            **group)
    grandchild = None
    try:
        with proc.stdout:
            grandchild = psutil.Process(int(proc.stdout.readline()))

        end_tree(proc)

        assert proc.poll() is not None
        _gone, alive = psutil.wait_procs([grandchild], timeout=WAIT_S)
        assert not alive, f'the grandchild {grandchild.pid} is still running'
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(WAIT_S)
        if grandchild is not None and grandchild.is_running():
            grandchild.kill()
