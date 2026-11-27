# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Test helper: an ``POST /api/enroll/`` body in the setup-rebuild FR-WK-8 shape.

``{"hostname", "nonce", "proof"}`` with a fresh 32-hex nonce and the proof
made from *key* by the manager's ``compute_proof``.
"""

import secrets

from workers.enrollment_key import compute_proof

KEY = "4F9XK2PBQ7M3N8RT"
OTHER_KEY = "00000000000000PP"   # valid shape, not the manager's key


def enroll_body(hostname, key=KEY, nonce=None, proof_hostname=None):
    """The body a worker holding *key* sends; ``proof_hostname`` signs another name than the one sent."""
    nonce = nonce or secrets.token_hex(16)
    proof = compute_proof(key, nonce, proof_hostname or hostname)
    return {"hostname": hostname, "nonce": nonce, "proof": proof}
