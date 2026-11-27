# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``--setup-cards``: this machine's graphics cards for the setup form (setup-rebuild FR-WK-1).

Input: ``{}``. Output::

    {"os_probe_failed": bool,
     "cards": [{"address", "key", "pci", "name", "brand", "vendor_id",
                "device_id", "revision_id", "screen", "primary_screen"}],
     "saved": {"compute", "cards", "combine", "env_override", "config_read_failed"},
     "unreal_enabled": bool, "unreal_enabled_env": bool,
     "enrollment": null | {"enrolled": true, "manager_id", "manager_name", "host", "port"},
     "ui_password_set": bool}

The cards are the card spec's OS card list (``cards.os_probe``) with the
screen marks (``cards.screens``), built with no Blender device list, so no
Blender is started or needed. ``address`` is the text ``--setup-apply``
takes in ``cards`` (a PCI address such as ``0000:23:00.0``, or
``apple-gpu`` on macOS). ``brand`` is ``NVIDIA``, ``AMD``, ``Intel``, ``Apple``
or ``null``; cards of more than one brand cannot be ticked together.
``saved`` is the current choice in ``config.json`` in the same field names
as ``--setup-apply``'s input, for a form that is filled again (Reset setup);
with ``config_read_failed`` its values are the defaults.

``enrollment`` and ``ui_password_set`` tell Reset setup what it may keep
(setup-rebuild FR-RS-3, FR-WZ-1). ``enrollment`` is ``null`` unless a
``manager.api_token`` and a ``manager.cert_fingerprint`` are stored (a
token without a pinned certificate cannot be used); then it holds the
stored ``manager.*`` values (``manager_name`` is ``manager.name`` as
``--setup-apply`` saved it), each ``null`` when missing or not usable:
text that is not printable or is too long (name 64, host 253, manager id
64), a port outside 1-65535. It never holds the token or
the certificate fingerprint. ``ui_password_set`` is true when a usable
worker page password hash is stored. These two read ``config.json``
strictly: when it cannot be read the answer is ``config_unreadable``.
"""
import logging

from sethlans_worker_agent import config_compute, config_store, unreal_switch
from sethlans_worker_agent.cards import brand, inventory, os_probe, screens
from sethlans_worker_agent.cards.identity import KEY_PREFIX, key_address
from sethlans_worker_agent.setup_cli import apply_fields
from sethlans_worker_agent.setup_cli.stdio import CliError, check_fields
from sethlans_worker_agent.web_ui import auth

logger = logging.getLogger(__name__)


def address_of(key: str) -> str:
    """The text a form sends for a card: the PCI address without ``pci:``, or ``apple-gpu``."""
    return key[len(KEY_PREFIX):] if key.startswith(KEY_PREFIX) else key


def _short(key: str):
    address = key_address(key)
    return f'{address[1]:02x}:{address[2]:02x}.{address[3]:x}' if address is not None else None


def probe() -> inventory.Inventory:
    """The OS card list with screen marks, joined with no Blender list (never starts Blender)."""
    return screens.marked(inventory.build(os_probe.probe(), None, None))


def _card(card: inventory.Card) -> dict:
    return {
        'address': address_of(card.key), 'key': card.key, 'pci': _short(card.key), 'name': card.name,
        'brand': brand.brand(card), 'vendor_id': card.vendor_id, 'device_id': card.device_id,
        'revision_id': card.revision_id, 'screen': card.screen, 'primary_screen': card.primary_screen,
    }


def _saved() -> dict:
    section = config_compute.load(strict=False)
    return {
        'compute': section.method,
        'cards': [address_of(key) for key in section.ticked_keys],
        'combine': section.gpu_mode == 'combined',
        'env_override': section.env_override,
        'config_read_failed': section.read_failed,
    }


MAX_HOST = 253
MAX_MANAGER_ID = 64


def _text(value, limit=None):
    """``value`` when it is non-empty text (printable and at most ``limit`` long when given), else ``None``."""
    if not isinstance(value, str) or not value:
        return None
    if limit is not None and (len(value) > limit or not value.isprintable()):
        return None
    return value


def _enrollment(data: dict):
    """The stored enrollment without its secrets, or ``None`` unless a token and fingerprint are stored."""
    section = data.get('manager')
    if (not isinstance(section, dict) or not _text(section.get('api_token'))
            or not _text(section.get('cert_fingerprint'))):
        return None
    port = section.get('port')
    return {
        'enrolled': True, 'manager_id': _text(section.get('manager_id'), MAX_MANAGER_ID),
        'manager_name': _text(section.get('name'), apply_fields.MAX_NAME),
        'host': _text(section.get('host'), MAX_HOST),
        'port': port if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536 else None,
    }


def _stored() -> dict:
    """``enrollment`` and ``ui_password_set``; ``config_unreadable`` when ``config.json`` cannot be read."""
    try:
        enrollment = _enrollment(config_store.load_strict())
        password_set = auth.current_hash() is not None
    except config_store.ConfigReadError as error:
        raise CliError('config_unreadable', f"The worker's settings file could not be read: {error}")
    return {'enrollment': enrollment, 'ui_password_set': password_set}


def run(request: dict) -> dict:
    check_fields(request, ())
    stored = _stored()
    found = probe()
    logger.info("Setup: listed %d graphics card(s) for the setup form%s.", len(found.cards),
                " (the operating system's card list could not be read)" if found.os_probe_failed else "")
    return {
        'os_probe_failed': found.os_probe_failed,
        'cards': [_card(card) for card in found.cards],
        'saved': _saved(),
        'unreal_enabled': unreal_switch.saved(),
        'unreal_enabled_env': unreal_switch.env_set(),
        **stored,
    }
