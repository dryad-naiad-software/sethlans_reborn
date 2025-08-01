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
from ..models import Animation, Job
from ..models import JobStatus
from ._base import BaseMediaTestCase

class AnimationSignalTests(BaseMediaTestCase):
    def setUp(self):
        super().setUp()
        # Asset created lazily in tests that need it

    def test_animation_total_time_updates_on_job_completion(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from ..models import Asset

        asset = Asset.objects.create(name="Test Asset for Signals", project=self.project,
                                     blend_file=SimpleUploadedFile("dummy_signal.blend", b"data"))
        anim = Animation.objects.create(name="Signal Test Animation", project=self.project, asset=asset,
                                        start_frame=1, end_frame=2)
        job1 = Job.objects.create(animation=anim, name="Job_1", asset=asset, render_time_seconds=100,
                                  status=JobStatus.QUEUED)
        job2 = Job.objects.create(animation=anim, name="Job_2", asset=asset, render_time_seconds=50,
                                  status=JobStatus.QUEUED)

        job1.status = JobStatus.DONE
        job1.save()

        anim.refresh_from_db()
        self.assertEqual(anim.total_render_time_seconds, 100)

        job2.status = JobStatus.DONE
        job2.save()

        anim.refresh_from_db()
        self.assertEqual(anim.total_render_time_seconds, 150)

