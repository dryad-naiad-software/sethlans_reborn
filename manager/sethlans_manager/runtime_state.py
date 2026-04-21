# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Process-local runtime state for the Sethlans Manager.

Holds values that are determined at process startup (after DB migrations
and TLS cert loading) and needed by request handlers.  Using a mutable
module as a singleton avoids the Django-settings-mutation anti-pattern.

All values are initialised to ``None``; the enroll view interprets any
``None`` as "not ready yet" and returns HTTP 503.
"""

import uuid
from typing import Optional

# Populated by ``run_manager.py`` after ``ManagerSettings`` is loaded.
manager_id: Optional[str] = None

# Populated at process startup — a fresh UUID4 string per manager boot.
# Used by ``GET /api/health/`` so the wizard's restart poll can detect
# when the manager has actually restarted (FR-14c / FR-15).  We generate
# it at module-load time so tests and short-lived utility processes get a
# non-empty value without needing to call ``runtime_init`` first;
# ``runtime_init.initialize_runtime_state`` overwrites it on full
# manager startup.
manager_boot_id: Optional[str] = uuid.uuid4().hex

# Populated by ``run_manager.py`` after the TLS cert is loaded — lowercase
# hex SHA-256 of the DER-encoded certificate.
cert_fingerprint: Optional[str] = None

# Optional broadcaster configuration fields — populated by ``run_manager.py``
# if the manager is running with the multicast broadcaster enabled.
# Published to ``<data_dir>/broadcaster_params.json`` for the launcher's
# broadcaster supervisor to read (the broadcaster itself lives in the
# launcher process, not inside Django).
broadcaster_name: Optional[str] = None
broadcaster_host: Optional[str] = None
broadcaster_ip: Optional[str] = None
broadcaster_port: Optional[int] = None
broadcaster_version: Optional[str] = None
