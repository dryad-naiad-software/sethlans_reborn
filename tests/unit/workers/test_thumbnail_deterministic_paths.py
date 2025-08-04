# FILENAME: workers/tests/test_thumbnail_deterministic_paths.py
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
# tests/unit/workers/test_thumbnail_deterministic_paths.py
import os
import tempfile
import pytest
from django.core.files.base import ContentFile
from django.utils.text import slugify
from PIL import Image

from workers.models import Project
from workers.models.projects import Asset
from workers.models.animations import Animation, AnimationFrame


def _png_bytes(color, size=(48, 48)):
    img = Image.new("RGB", size, color=color)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    try:
        img.save(tmp, "PNG")
        tmp.close()
        with open(tmp.name, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp.name)
        except FileNotFoundError:
            pass


@pytest.mark.django_db
def test_animation_thumbnail_deterministic_and_replaced(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    # Enable deletion so storage doesn't generate a new unique name
    settings.WORKERS_DELETE_OLD_THUMBNAILS = True

    proj = Project.objects.create(name="deterministic-proj")
    asset = Asset.objects.create(name="deterministic-asset", project=proj, blend_file=b"x")

    anim = Animation.objects.create(
        name="deterministic-anim",
        project=proj,
        asset=asset,
        start_frame=1,
        end_frame=2,
        frame_step=1,
    )

    # Render first frame -> creates both frame and animation thumbnails
    f1 = AnimationFrame.objects.create(animation=anim, frame_number=1)
    f1.output_file.save("frame1.png", ContentFile(_png_bytes((255, 0, 0))), save=True)

    # Record the animation thumbnail path after first update
    path1 = anim.thumbnail.path
    assert os.path.exists(path1)

    # The path should be deterministic and descriptive
    expected_basename = f"{slugify(anim.name)}-{anim.pk}_thumbnail.png"
    assert os.path.basename(path1) == expected_basename

    # Render second frame -> animation thumbnail should be updated in place
    f2 = AnimationFrame.objects.create(animation=anim, frame_number=2)
    f2.output_file.save("frame2.png", ContentFile(_png_bytes((0, 255, 0))), save=True)

    anim.refresh_from_db()
    path2 = anim.thumbnail.path

    # The path must be identical (deterministic), but the file content has been replaced.
    assert path1 == path2
    assert os.path.exists(path2)