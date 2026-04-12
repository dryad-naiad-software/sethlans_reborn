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

from typing import Optional

# Populated by ``run_manager.py`` after ``ManagerSettings`` is loaded.
manager_id: Optional[str] = None

# Populated by ``run_manager.py`` after the TLS cert is loaded — lowercase
# hex SHA-256 of the DER-encoded certificate.
cert_fingerprint: Optional[str] = None

# Optional broadcaster configuration fields — populated by ``run_manager.py``
# if the manager is running with the multicast broadcaster enabled.  Read
# by the ASGI lifespan startup hook in ``sethlans_manager/asgi.py``.
broadcaster_name: Optional[str] = None
broadcaster_host: Optional[str] = None
broadcaster_ip: Optional[str] = None
broadcaster_port: Optional[int] = None
broadcaster_version: Optional[str] = None
