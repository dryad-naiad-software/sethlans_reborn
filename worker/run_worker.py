# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A user-friendly script to run the Sethlans Reborn Worker Agent.

This script ensures the worker package is importable and then launches
the agent's main loop. It provides a simple, one-command entry point.
"""
import sys
from pathlib import Path

# In frozen mode PyInstaller handles sys.path; only add the worker
# directory and project root when running from source.
if not getattr(sys, 'frozen', False):
    worker_dir = str(Path(__file__).resolve().parent)
    if worker_dir not in sys.path:
        sys.path.insert(0, worker_dir)
    # Ensure shared/ is importable (project root = parent of worker/).
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

if __name__ == "__main__":
    from sethlans_worker_agent.agent import main
    main()
