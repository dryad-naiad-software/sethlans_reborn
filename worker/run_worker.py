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

# Reserved first argument (Unreal foundation FR-UE-27): a frozen worker runs
# the known-folder probe as a child of itself. It is handled before any
# other import or startup work and never reaches the agent.
KNOWN_FOLDER_PROBE_ARG = '--sethlans-known-folder-probe'


def _run_known_folder_probe_if_requested(argv) -> None:
    if len(argv) > 1 and argv[1] == KNOWN_FOLDER_PROBE_ARG:
        from sethlans_worker_agent.unreal_executor import known_folder_probe
        sys.exit(known_folder_probe.main())


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
    _run_known_folder_probe_if_requested(sys.argv)
    from sethlans_worker_agent.agent import main
    main()
