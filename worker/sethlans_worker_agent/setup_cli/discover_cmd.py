# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``--setup-discover``: the managers announcing themselves on this network (setup-rebuild FR-WK-1).

Input: ``{"seconds": <number>}``, optional, default 5, more than 0 and at
most 15: how long to listen to the manager broadcasts
(``multicast_listener``, UDP 239.150.74.50:8082). Output::

    {"managers": [{"name", "host", "ip", "port", "manager_id", "version"}]}

sorted by name, then manager id; one entry per manager id (the latest
announcement wins). The listening socket could not be opened: exit 1,
``discovery_failed``.
"""
import logging

from sethlans_worker_agent.multicast_listener import MulticastListener
from sethlans_worker_agent.setup_cli.stdio import CliError, check_fields

logger = logging.getLogger(__name__)

DEFAULT_SECONDS = 5
MAX_SECONDS = 15


def _seconds(request: dict) -> float:
    value = request.get('seconds', DEFAULT_SECONDS)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= MAX_SECONDS:
        raise CliError('bad_input', f"seconds must be a number more than 0 and at most {MAX_SECONDS}.")
    return float(value)


def _entry(announcement: dict) -> dict:
    return {
        'name': announcement.get('name', ''), 'host': announcement.get('host', ''),
        'ip': announcement.get('ip', ''), 'port': announcement.get('port', 8080),
        'manager_id': announcement.get('manager_id', ''), 'version': announcement.get('version', ''),
    }


def run(request: dict) -> dict:
    check_fields(request, ('seconds',))
    seconds = _seconds(request)
    try:
        announcements = MulticastListener(timeout=seconds).discover()
    except OSError as error:
        logger.error("Setup: could not listen for manager broadcasts: %s", error)
        raise CliError('discovery_failed', f"Could not listen for manager broadcasts on UDP port 8082: {error}")
    managers = sorted((_entry(found) for found in announcements.values()),
                      key=lambda entry: (str(entry['name']), str(entry['manager_id'])))
    logger.info("Setup: found %d manager(s) in %.0f s.", len(managers), seconds)
    return {'managers': managers}
