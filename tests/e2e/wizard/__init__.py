# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""End-to-end tests for the wizard hand-off lifecycle (Spec 1 / D2).

Exercises the full launcher ↔ wizard ↔ runtime hand-off pipeline:

* The launcher spawns the wizard subprocess, hands it the setup token
  + IPC HMAC secret via chmod-600 files in ``<data_dir>/wizard/``.
* A test client drives the wizard's HTTP API (auth → topology → done).
* The wizard writes the HMAC-signed ``.wizard_done`` marker.
* The launcher reads the marker, validates it, spawns the runtime
  (a mock HTTPS server bound on the FR-W14a-hardcoded probe port).
* The wizard's ``/runtime-ready/`` endpoint flips from ``booting`` to
  ``ready`` once the mock is up; or to ``failed`` when the runtime
  exits early and the launcher writes ``.runtime_failed``.

Tests are sequential (port 8080 / 8081 collisions otherwise) and skip
gracefully when those ports are busy on the host.
"""
