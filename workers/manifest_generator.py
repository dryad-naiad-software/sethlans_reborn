# workers/manifest_generator.py
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
# Created by Gemini on 8/2/2025.
# Dryad and Naiad Software LLC
#
# Project: sethlans_reborn
#
"""
A utility for generating a human-readable manifest file for each project.

This module provides a function to create or update a 'manifest.txt' file within
a project's asset directory. The manifest lists the project's details, its
associated assets, and all render jobs, making it easier for users to
understand the contents of the otherwise UUID-named directories and files.
"""

import logging
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from .models import Project, Job, Animation, TiledJob, Asset

logger = logging.getLogger(__name__)


def update_project_manifest(project_id: str):
    """
    Generates or updates the manifest.txt file for a given project.

    This function gathers all relevant information about a project—its assets,
    animations, tiled jobs, and standalone jobs—and writes it into a single,
    human-readable text file in the project's main asset directory.

    Args:
        project_id (str): The UUID of the project to generate a manifest for.
    """
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        logger.error(f"Cannot generate manifest: Project with ID {project_id} not found.")
        return

    # Define the path for the manifest file
    manifest_path = Path(settings.MEDIA_ROOT) / 'assets' / str(project.id) / 'manifest.txt'
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    # Gather data
    assets = Asset.objects.filter(project=project)
    animations = Animation.objects.filter(project=project)
    tiled_jobs = TiledJob.objects.filter(project=project)
    # Standalone jobs are linked via asset, not directly to the project
    standalone_jobs = Job.objects.filter(
        asset__project=project,
        animation__isnull=True,
        tiled_job__isnull=True
    )

    # Build the manifest content
    content = []
    content.append("Project Manifest")
    content.append("=" * 20)
    content.append(f"Project Name: {project.name}")
    content.append(f"Project UUID: {project.id}")
    content.append(f"Generated: {timezone.now().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    content.append("\n" + ("-" * 3) + " Assets " + ("-" * 3))

    if not assets:
        content.append("No assets found for this project.")
    else:
        for asset in assets:
            file_name = Path(asset.blend_file.name).name
            content.append(f"- {asset.name} (File: {file_name})")

    content.append("\n" + ("-" * 3) + " Render Jobs " + ("-" * 3))

    job_found = False
    if animations:
        job_found = True
        for anim in animations:
            content.append(f"\n[Animation] {anim.name}")
            content.append(f"  - Asset: {anim.asset.name}")

    if tiled_jobs:
        job_found = True
        for tj in tiled_jobs:
            content.append(f"\n[Tiled Job] {tj.name}")
            content.append(f"  - Asset: {tj.asset.name}")

    if standalone_jobs:
        job_found = True
        for job in standalone_jobs:
            content.append(f"\n[Job] {job.name}")
            content.append(f"  - Asset: {job.asset.name}")

    if not job_found:
        content.append("No jobs found for this project.")

    # Write to file
    try:
        with open(manifest_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(content))
        logger.info(f"Successfully updated manifest for project '{project.name}'.")
    except IOError as e:
        logger.error(f"Failed to write manifest for project '{project.name}': {e}")