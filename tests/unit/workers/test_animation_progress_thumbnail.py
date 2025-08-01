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

