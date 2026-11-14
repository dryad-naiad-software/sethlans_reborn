# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
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

from .constants import AssetSourceType
from .models import Project, Job, Animation, TiledJob, Asset
from .renderers import UnsupportedRendererError, strategy_for_source_type

logger = logging.getLogger(__name__)


def _describe_asset(asset):
    """Describe *asset* through its source type's strategy (FR-MR-12).

    Chosen by ``source_type``, not ``asset.project.renderer``, so the loop
    makes no extra query per asset.  An unknown value is described as a
    ``.blend`` file, the field's default.
    """
    try:
        strategy = strategy_for_source_type(asset.source_type)
    except UnsupportedRendererError:
        strategy = strategy_for_source_type(AssetSourceType.BLEND_FILE)
    return strategy.describe_asset(asset)


def _asset_lines(assets):
    if not assets:
        return ["No assets found for this project."]
    return [f"- {asset.name} ({_describe_asset(asset)})" for asset in assets]


def _job_lines(animations, tiled_jobs, standalone_jobs):
    lines = []
    for heading, rows in (
        ('Animation', animations), ('Tiled Job', tiled_jobs), ('Job', standalone_jobs),
    ):
        for row in rows:
            lines.append(f"\n[{heading}] {row.name}")
            lines.append(f"  - Asset: {row.asset.name}")
    return lines or ["No jobs found for this project."]


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

    try:
        # Define the path for the manifest file using the short UUID
        project_short_id = str(project.id)[:8]
        manifest_path = Path(settings.MEDIA_ROOT) / 'assets' / project_short_id / 'manifest.txt'
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

        content.extend(_asset_lines(assets))
        content.append("\n" + ("-" * 3) + " Render Jobs " + ("-" * 3))
        content.extend(_job_lines(animations, tiled_jobs, standalone_jobs))

        # Write to file
        with open(manifest_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(content))
        logger.info(f"Successfully updated manifest for project '{project.name}'.")
    except Exception as e:
        logger.error(f"Failed to generate manifest for project '{project.name}': {e}")
