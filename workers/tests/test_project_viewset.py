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
# Created by Mario Estrella on 8/1/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
from rest_framework import status
from ._base import BaseMediaTestCase

class ProjectViewSetTests(BaseMediaTestCase):
    def test_pause_project(self):
        self.assertFalse(self.project.is_paused)
        url = f"/api/projects/{self.project.id}/pause/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_paused'])
        self.project.refresh_from_db()
        self.assertTrue(self.project.is_paused)

    def test_unpause_project(self):
        self.project.is_paused = True
        self.project.save()
        self.assertTrue(self.project.is_paused)

        url = f"/api/projects/{self.project.id}/unpause/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['is_paused'])
        self.project.refresh_from_db()
        self.assertFalse(self.project.is_paused)

    def test_create_project_name_too_short(self):
        """
        Tests that creating a project with a name less than 4 characters fails.
        """
        url = "/api/projects/"
        data = {"name": "abc"}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)
        self.assertIn("at least 4 characters", str(response.data['name']))

    def test_create_project_name_too_long(self):
        """
        Tests that creating a project with a name more than 40 characters fails.
        """
        url = "/api/projects/"
        data = {"name": "a" * 41}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)
        self.assertIn("more than 40 characters", str(response.data['name']))