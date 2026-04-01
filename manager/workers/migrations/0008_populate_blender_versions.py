# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

from django.db import migrations


# Hardcoded version mapping -- no network access in migrations.
KNOWN_VERSIONS = {
    "4.0": "4.0.2",
    "4.1": "4.1.1",
    "4.2": "4.2.19",
    "4.3": "4.3.2",
    "4.4": "4.4.3",
    "4.5": "4.5.8",
    "5.0": "5.0.1",
    "5.1": "5.1.0",
}


def _extract_series(version_str):
    """Extract major.minor series from a version string."""
    parts = version_str.strip().split('.')
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return version_str.strip()


def _collect_existing_series(apps):
    """Gather unique major.minor series from existing job records."""
    series_set = set()
    for model_name in ('Job', 'TiledJob', 'Animation'):
        Model = apps.get_model('workers', model_name)
        for val in Model.objects.values_list(
            'blender_version_old', flat=True,
        ).distinct():
            if val:
                series_set.add(_extract_series(val))
    return series_set or {"4.5"}


def _create_version_rows(SBV, series_set):
    """Create SupportedBlenderVersion rows; return {series: instance}."""
    created = {}
    for series in sorted(series_set):
        parts = series.split('.')
        resolved = KNOWN_VERSIONS.get(series, f"{series}.0")
        sbv = SBV.objects.create(
            major=int(parts[0]),
            minor=int(parts[1]),
            series=series,
            resolved_version=resolved,
            is_default=False,
        )
        created[series] = sbv

    if created:
        highest = max(
            created.keys(),
            key=lambda s: [int(p) for p in s.split('.')],
        )
        SBV.objects.filter(pk=created[highest].pk).update(is_default=True)
    return created


def _link_fks(apps, created_versions):
    """Update FK references on Projects, Jobs, TiledJobs, Animations."""
    SBV = apps.get_model('workers', 'SupportedBlenderVersion')
    Project = apps.get_model('workers', 'Project')

    default_sbv = SBV.objects.filter(is_default=True).first()
    if not default_sbv:
        default_sbv = SBV.objects.order_by('-major', '-minor').first()
    if default_sbv:
        Project.objects.filter(
            blender_version__isnull=True,
        ).update(blender_version=default_sbv)

    for series, sbv in created_versions.items():
        for model_name in ('Job', 'TiledJob', 'Animation'):
            Model = apps.get_model('workers', model_name)
            Model.objects.filter(
                blender_version__isnull=True,
                blender_version_old__startswith=series,
            ).update(blender_version=sbv)


def populate_versions(apps, schema_editor):
    """Create SupportedBlenderVersion rows and link FKs."""
    SBV = apps.get_model('workers', 'SupportedBlenderVersion')
    series_set = _collect_existing_series(apps)
    created = _create_version_rows(SBV, series_set)
    _link_fks(apps, created)


def reverse_populate(apps, schema_editor):
    """Reverse: clear FK values (old CharFields still exist)."""
    for model_name in ('Job', 'TiledJob', 'Animation', 'Project'):
        Model = apps.get_model('workers', model_name)
        Model.objects.all().update(blender_version=None)


class Migration(migrations.Migration):
    """
    Data migration (step 2 of 3): Populate SupportedBlenderVersion rows
    from existing data and link FK references.
    """

    dependencies = [
        ('workers', '0007_add_supported_blender_version_and_fks'),
    ]

    operations = [
        migrations.RunPython(populate_versions, reverse_populate),
    ]
