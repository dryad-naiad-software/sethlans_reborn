# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Shared constants for wizard test files.

``test_wizard.py`` (interactive path) and ``test_wizard_unattended.py``
(unattended path) both consume the same announcement fixtures and the
same mock enrollment result. Keeping them here keeps each test file
under the 300-line cap without duplicating literal payloads.
"""


VALID_RESULT = {
    "api_token": "a" * 40,
    "cert_fingerprint": "b" * 64,
    "manager_id": "00000000-0000-0000-0000-000000000042",
}


ANNOUNCEMENT_ONE = {
    "00000000-0000-0000-0000-000000000042": {
        "v": 1,
        "manager_id": "00000000-0000-0000-0000-000000000042",
        "name": "Lab Manager",
        "host": "lab.example",
        "ip": "10.0.0.1",
        "port": 8080,
        "version": "0.1.0",
    },
}


ANNOUNCEMENT_TWO = {
    "00000000-0000-0000-0000-000000000042": ANNOUNCEMENT_ONE[
        "00000000-0000-0000-0000-000000000042"
    ],
    "00000000-0000-0000-0000-000000000099": {
        "v": 1,
        "manager_id": "00000000-0000-0000-0000-000000000099",
        "name": "Studio Manager",
        "host": "studio.example",
        "ip": "10.0.0.2",
        "port": 8080,
        "version": "0.1.0",
    },
}
