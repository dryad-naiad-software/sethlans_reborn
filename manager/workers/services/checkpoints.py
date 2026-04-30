# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Canonical names for setup wizard checkpoints.

The strings here are the wire format: they are persisted in the
sentinel JSON's ``checkpoints[]`` list and matched by the frontend's
``setup-step-config.ts:checkpoint`` field.  DO NOT rename them —
that would invalidate every existing sentinel on disk and break
in-flight installations.

Add new checkpoints here rather than introducing fresh string
literals at call sites.
"""

TOPOLOGY_CHOSEN = "topology_chosen"
NETWORK_CONFIGURED = "network_configured"
DATABASE_CONFIGURED = "database_configured"
ADMIN_CREATED = "admin_created"
WORKER_PASSWORD_SET = "worker_password_set"
BLENDER_PREDOWNLOADED = "blender_predownloaded"
VERIFIED = "verified"
