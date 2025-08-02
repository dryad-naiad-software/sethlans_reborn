# FILENAME: tests/e2e/test_concurrency.py
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
# Created by Mario Estrella on 8/2/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
"""
End-to-end tests for concurrency and database stability. 🏎️💨
"""

import requests
import threading
import uuid  # <-- Import uuid
from .shared_setup import BaseE2ETest, MANAGER_URL
from .helpers import poll_for_completion
from workers.constants import RenderSettings


class TestConcurrency(BaseE2ETest):
    """
    Validates the system's stability under high-concurrency scenarios,
    specifically to prevent database locking errors.
    """

    def test_high_concurrency_animation_submission(self):
        """
        Tests system stability by submitting multiple animation jobs in quick
        succession to simulate a high-load scenario that could cause
        database lock contention. This serves as a regression test for the
        'database is locked' SQLite error.
        """
        print("\n--- E2E TEST: High-Concurrency Animation Submission ---")

        num_animations_to_submit = 3
        animation_urls = []
        threads = []

        def submit_animation(url_list):
            """Target function for each thread to submit an animation job."""
            # Use UUID to guarantee a unique name for each concurrent request
            unique_id = uuid.uuid4()
            anim_payload = {
                "name": f"E2E Concurrency Test Animation {unique_id}",
                "project": self.project_id,
                "asset_id": self.anim_asset_id,
                "output_file_pattern": f"concurrent_anim_{unique_id}_####",
                "start_frame": 1,
                "end_frame": 2,  # Short animation
                "blender_version": self._blender_version_for_test,
                "render_settings": {
                    RenderSettings.SAMPLES: 8,
                    RenderSettings.RESOLUTION_PERCENTAGE: 10
                }
            }
            try:
                create_response = requests.post(f"{MANAGER_URL}/animations/", json=anim_payload)
                create_response.raise_for_status()
                anim_id = create_response.json()['id']
                anim_url = f"{MANAGER_URL}/animations/{anim_id}/"
                url_list.append(anim_url)
                print(f"  Successfully submitted animation: {anim_url}")
            except requests.RequestException as e:
                print(f"  Error submitting animation: {e}")

        # Launch threads to submit animations concurrently
        print(f"Submitting {num_animations_to_submit} animations concurrently...")
        for _ in range(num_animations_to_submit):
            thread = threading.Thread(target=submit_animation, args=(animation_urls,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(animation_urls) == num_animations_to_submit, "Not all animations were submitted successfully."

        # Poll for completion of all submitted animations
        print("\nPolling for completion of all concurrent animations...")
        for anim_url in animation_urls:
            try:
                poll_for_completion(anim_url, timeout_seconds=180)
                print(f"  Animation {anim_url} completed successfully.")
            except Exception as e:
                self.fail(f"Polling failed for animation {anim_url}: {e}")

        print("\nSUCCESS: All concurrent animation jobs completed without database errors.")