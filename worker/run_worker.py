# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 3/31/2026.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# run_worker.py
"""
A user-friendly script to run the Sethlans Reborn Worker Agent.

This script ensures the worker package is importable and then launches
the agent's main loop. It provides a simple, one-command entry point.
"""
import sys
from pathlib import Path

# Add the worker directory to sys.path so sethlans_worker_agent is importable.
worker_dir = str(Path(__file__).resolve().parent)
if worker_dir not in sys.path:
    sys.path.insert(0, worker_dir)

if __name__ == "__main__":
    from sethlans_worker_agent.agent import main
    main()
