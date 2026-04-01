# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import os
import shutil
import tempfile
from django.test import override_settings
from rest_framework.test import APITestCase
from ..models import Project, SupportedBlenderVersion


class BaseMediaTestCase(APITestCase):
    """
    Creates a temporary MEDIA_ROOT for file-upload tests and a default Project.
    Cleans up temp files afterward.
    """
    _media_root_override = None
    media_root = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp()
        cls._media_root_override = override_settings(MEDIA_ROOT=cls.media_root)
        cls._media_root_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_root_override.disable()
        if cls.media_root and os.path.exists(cls.media_root):
            shutil.rmtree(cls.media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.default_version = SupportedBlenderVersion.objects.create(
            major=4, minor=5, series="4.5",
            resolved_version="4.5.8", is_default=True,
        )
        self.project = Project.objects.create(
            name="Default Test Project",
            blender_version=self.default_version,
        )
