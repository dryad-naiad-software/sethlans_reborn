# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
#
# Created by Mario Estrella on 8/1/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/unit/workers/test_animation_progress_thumbnail.py

import os
import tempfile

import pytest
from django.core.files.base import ContentFile
from PIL import Image

from workers.models import Project
from workers.models.projects import Asset
from workers.models.animations import Animation, AnimationFrame
from workers.constants import TilingConfiguration


def _png_bytes(color, size=(32, 32)):
    img = Image.new("RGB", size, color=color)
    buf = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    try:
        img.save(buf, "PNG")
        buf.close()
        with open(buf.name, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(buf.name)
        except FileNotFoundError:
            pass


@pytest.mark.django_db
def test_animation_thumbnail_updates_on_each_frame(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path.as_posix()

    project = Project.objects.create(name="anim-progress-proj")
    asset = Asset.objects.create(name="anim-progress-asset", project=project, blend_file=b"dummy")

    # Using 2 frames is enough to prove progression
    animation = Animation.objects.create(
        name="anim-progress",
        project=project,
        asset=asset,
        start_frame=1,
        end_frame=2,
        frame_step=1,
        tiling_config=TilingConfiguration.NONE,  # not relevant for this test
    )

    # Frame 1 -> red
    f1 = AnimationFrame.objects.create(animation=animation, frame_number=1)
    red_bytes = _png_bytes((255, 0, 0))
    f1.output_file.save("f1.png", ContentFile(red_bytes), save=True)

    with open(animation.thumbnail.path, "rb") as fh:
        first_thumb = fh.read()

    # Frame 2 -> green (should overwrite/refresh animation thumbnail)
    f2 = AnimationFrame.objects.create(animation=animation, frame_number=2)
    green_bytes = _png_bytes((0, 255, 0))
    f2.output_file.save("f2.png", ContentFile(green_bytes), save=True)

    with open(animation.thumbnail.path, "rb") as fh:
        second_thumb = fh.read()

    assert first_thumb != second_thumb, "Animation thumbnail should update as new frames complete"

