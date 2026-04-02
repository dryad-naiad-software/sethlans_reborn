# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import os
import shutil
import tempfile
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient, APITestCase
from ..models import Project, SupportedBlenderVersion, Worker


class BaseMediaTestCase(APITestCase):
    """
    Creates a temporary MEDIA_ROOT for file-upload tests and a default
    Project.  Also creates an admin user and force-authenticates the
    test client so that views protected by IsAdmin permissions are
    accessible.  Cleans up temp files afterward.
    """
    _media_root_override = None
    media_root = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp()
        cls._media_root_override = override_settings(
            MEDIA_ROOT=cls.media_root,
        )
        cls._media_root_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_root_override.disable()
        if cls.media_root and os.path.exists(cls.media_root):
            shutil.rmtree(cls.media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        User = get_user_model()
        self.admin_user = User.objects.create_superuser(
            username='testadmin', password='testpass123',
        )
        self.client.force_authenticate(user=self.admin_user)

        self.default_version, _ = (
            SupportedBlenderVersion.objects.get_or_create(
                series="4.5",
                defaults=dict(
                    major=4, minor=5,
                    resolved_version="4.5.8", is_default=True,
                ),
            )
        )
        self.project = Project.objects.create(
            name="Default Test Project",
            blender_version=self.default_version,
        )

    def _make_worker_client(self, hostname="test-worker",
                            available_tools=None):
        """
        Create a Worker with a linked User and Token, and return
        an APIClient force-authenticated as that worker.

        If *available_tools* is ``None``, a default set containing
        the blender version from ``self.default_version`` is used.
        """
        User = get_user_model()
        if available_tools is None:
            available_tools = {
                "blender": [self.default_version.resolved_version],
            }
        worker_user = User.objects.create_user(
            username=f"worker_{hostname}", password="workerpass",
        )
        token = Token.objects.create(user=worker_user)
        worker = Worker.objects.create(
            hostname=hostname, user=worker_user,
            available_tools=available_tools,
        )
        client = APIClient()
        client.force_authenticate(user=worker_user, token=token)
        return client, worker
