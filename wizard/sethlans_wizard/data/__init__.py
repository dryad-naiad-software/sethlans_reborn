# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bundled data resources for the wizard.

* ``common-passwords.txt`` — lowercase password list for the
  ``CommonPasswordValidator`` (FR-M2-5). The wizard verifies the
  resource SHA-256 at startup against
  :data:`wizard.sethlans_wizard.password_validators.COMMON_PASSWORDS_SHA256`.

PyInstaller's ``datas`` list MUST include the ``.txt`` file so the
frozen wizard bundle can find it at runtime; this ``__init__.py`` is
the marker that ``importlib.resources.files(...)`` resolves the
package against.
"""
