# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
MySQL and MariaDB go through PyMySQL, pure Python, on every OS.

Django's MySQL backend imports ``MySQLdb`` (the C ``mysqlclient``).
``pymysql.install_as_MySQLdb()`` puts PyMySQL in ``sys.modules`` under
that name, so the backend loads PyMySQL instead; with PyMySQL 1.2.4 the
module reports ``version_info = (2, 2, 8, 'final', 1)``, which passes
Django 5.2's ``>= (1, 4, 3)`` check. ``mysqlclient`` is never used, even
when it is installed.

Both settings modules import this module before anything else, so it
runs before Django can load a MySQL backend.
"""
import pymysql

pymysql.install_as_MySQLdb()
