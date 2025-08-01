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
# tests/unit/workers/test_thumbnail_overwrite.py

import os
import tempfile
import pytest
from django.core.files.base import ContentFile
from PIL import Image

from workers.models import Project
from workers.models.projects import Asset
from workers.models.animations import Animation, AnimationFrame


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
def test_animation_thumbnail_old_file_deleted(settings, tmp_path):
    """
    With WORKERS_DELETE_OLD_THUMBNAILS=True, saving a new animation thumbnail
    should replace the file in place at the deterministic path.
    """
    settings.MEDIA_ROOT = tmp_path.as_posix()
    settings.WORKERS_DELETE_OLD_THUMBNAILS = True

    project = Project.objects.create(name="thumb-clean-proj")
    asset = Asset.objects.create(name="thumb-clean-asset", project=project, blend_file=b"dummy")

    animation = Animation.objects.create(
        name="thumb-clean-anim",
        project=project,
        asset=asset,
        start_frame=1,
        end_frame=2,
        frame_step=1,
    )

    # Frame 1 -> writes first animation thumbnail
    f1 = AnimationFrame.objects.create(animation=animation, frame_number=1)
    red = _png_bytes((255, 0, 0))
    f1.output_file.save("f1.png", ContentFile(red), save=True)

    first_thumb_path = animation.thumbnail.path
    assert os.path.exists(first_thumb_path)
    # Deterministic basename '<pk>.png'
    assert os.path.basename(first_thumb_path) == f"{animation.pk}.png"

    with open(first_thumb_path, "rb") as fh:
        bytes_before = fh.read()

    # Frame 2 -> writes second animation thumbnail; should replace in place
    f2 = AnimationFrame.objects.create(animation=animation, frame_number=2)
    green = _png_bytes((0, 255, 0))
    f2.output_file.save("f2.png", ContentFile(green), save=True)

    animation.refresh_from_db()
    second_thumb_path = animation.thumbnail.path

    # Path is identical (deterministic) and file exists
    assert second_thumb_path == first_thumb_path
    assert os.path.exists(second_thumb_path)

    # Bytes should differ, proving the old file was deleted+replaced in place
    with open(second_thumb_path, "rb") as fh:
        bytes_after = fh.read()

    assert bytes_before != bytes_after, "Animation thumbnail should be replaced in place at the same path"
