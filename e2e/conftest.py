#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Mario Estrella on 7/29/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/e2e/conftest.py
import shutil
import tempfile
from pathlib import Path

def pytest_sessionfinish(session, exitstatus):
    """
    Pytest hook that runs once at the end of the entire test session.
    Used here to clean up the persistent Blender E2E cache directory.
    """
    cache_root = Path(tempfile.gettempdir()) / "sethlans_e2e_cache"
    print(f"\n--- E2E Session Teardown: Cleaning up cache at {cache_root} ---")
    if cache_root.exists():
        try:
            shutil.rmtree(cache_root)
            print(f"Successfully removed cache directory: {cache_root}")
        except Exception as e:
            print(f"Error removing cache directory {cache_root}: {e}")
