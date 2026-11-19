# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Unreal on/off switch on the worker's settings page (card selection O14, C-27 a).

The switch is the existing ``[unreal] ENABLED`` setting: environment
``SETHLANS_UNREAL_ENABLED`` > JSON config store ``unreal.enabled`` > legacy
``config.ini`` > ``False`` (``config_unreal``). ``config_unreal`` reads it once,
at import, so a change takes effect at the next worker start. It is not part
of the card choice and the manager cannot change it (D17).

- ``state()``: ``unreal_enabled`` (the value the next start will use),
  ``unreal_enabled_env`` (set by the environment variable, so the page
  cannot change it) and ``unreal_restart_needed`` (that value differs from
  the running one). Added to both card GET answers (``web_ui.cards_view``).
- ``save(enabled, source)``: writes ``unreal.enabled`` through
  ``config_store.set`` (its own locks); ``EnvOverride`` when the
  environment variable is set. One INFO line.

Holds no lock of its own; reads the config file without a lock (it is
replaced whole on every write).
"""
import logging
import os

from sethlans_worker_agent import config, config_store, config_unreal

logger = logging.getLogger(__name__)

ENV_VAR = 'SETHLANS_UNREAL_ENABLED'
KEY = 'unreal.enabled'


class EnvOverride(Exception):
    """The switch is set by ``SETHLANS_UNREAL_ENABLED``."""


def env_set() -> bool:
    return ENV_VAR in os.environ


def _fresh_value(section: str, key: str, default):
    """``config.get_config_value`` with the JSON config store read now, not at import."""
    if env_set():
        return os.environ[ENV_VAR]
    value = config_store.get(f'{section}.{key}', None)
    return value if value is not None else config.get_config_value(section, key, default)


def saved() -> bool:
    """The value of ``[unreal] ENABLED`` the next worker start will use."""
    return config_unreal._as_bool(_fresh_value, 'ENABLED', False)  # the loader's own parsing, this key only


def state() -> dict:
    value = saved()
    return {'unreal_enabled': value, 'unreal_enabled_env': env_set(),
            'unreal_restart_needed': value != config_unreal.ENABLED}


def save(enabled: bool, source: str) -> None:
    """Save the switch for the next start; ``EnvOverride`` when the environment sets it."""
    if env_set():
        raise EnvOverride()
    config_store.set(KEY, bool(enabled))
    logger.info("Unreal switched %s from the %s; this takes effect at the next worker start.",
                'on' if enabled else 'off', source)
