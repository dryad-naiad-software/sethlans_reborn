# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The renderer strategy interface (Unreal foundation spec FR-MR-7/8).

Serializers and views never branch on a renderer value: they look up a
strategy once (``get_strategy`` / ``strategy_for_source_type``) and call
its methods.  This module imports no DRF.

Error contract:

* **Request-path methods** (``validate_*``, ``prepare_*``, ``poll_q``)
  raise ``rest_framework.exceptions.ValidationError``.  Django's own
  ``ValidationError`` must not be used there: raised from ``get_queryset``
  (the poll path) DRF does not translate it and it becomes a 500.
* **Methods called from non-request code** (``claim_rejection``,
  ``routing_note``, ``describe_asset``, ``supports`` and the flag
  methods) never raise a DRF exception; they return values.
"""

from abc import ABC, abstractmethod
from contextlib import nullcontext


class UnsupportedRendererError(KeyError):
    """No strategy is registered for a renderer or asset source type."""


class RendererStrategy(ABC):
    """Renderer-specific rules for projects, assets, jobs and routing.

    Attributes:
        renderer: The ``Renderer`` value this strategy handles.
        source_type: The ``AssetSourceType`` value of its assets.
        label: Short human name used in shared error messages.
    """

    renderer = None
    source_type = None
    label = None

    @abstractmethod
    def validate_project(self, attrs, instance):
        """Validate project ``attrs`` (``instance`` on update); return attrs."""

    @abstractmethod
    def prepare_project_create(self, validated):
        """Complete validated project data just before the row is created."""

    @abstractmethod
    def validate_asset(self, attrs, instance):
        """Validate asset ``attrs`` (``instance`` on update); return attrs."""

    @abstractmethod
    def validate_job(self, attrs, asset, instance):
        """Validate job ``attrs`` for ``asset`` (``instance`` on update)."""

    @abstractmethod
    def prepare_job_create(self, validated, asset):
        """Complete validated job data just before the row is created."""

    @abstractmethod
    def poll_q(self, params, worker=None):
        """Return the ``Q`` selecting this renderer's jobs for a worker poll.

        ``worker`` is the polling caller's ``Worker`` row, or ``None`` for a
        non-worker caller, so a strategy can leave out jobs its
        ``claim_rejection`` would refuse for that worker (FR-MS-5).
        """

    @abstractmethod
    def claim_rejection(self, worker, job):
        """Return why ``worker`` may not claim ``job``, or ``None``."""

    @abstractmethod
    def routing_note(self, job, context):
        """Return a human note on why ``job`` is not being picked up, or None."""

    @abstractmethod
    def describe_asset(self, asset):
        """Return a one-line description of ``asset`` for the manifest."""

    @abstractmethod
    def supports(self, feature):
        """Return whether a job type (``'tiled_job'``, ``'animation'``) is supported."""

    @abstractmethod
    def idempotent_reclaim(self):
        """Return whether a repeated claim by the assignee returns 200 (FR-RT-4)."""

    @abstractmethod
    def worker_patch_allowed_fields(self):
        """Return the keys a worker PATCH may send before validation, or None.

        ``None`` adds no restriction.  Whatever this returns is checked on
        top of (never instead of) ``WORKER_PATCH_FIELDS``, the allowlist
        every worker PATCH already passes (issue #217).
        """

    @abstractmethod
    def requeue_on_worker_restart(self):
        """Return whether a worker restart requeues this renderer's jobs."""

    @abstractmethod
    def supports_release(self):
        """Return whether ``POST /api/jobs/{id}/release/`` applies."""

    # -- Measured-peak gate hooks (FR-MR-8, v4.3/v4.4; chunk 2b) ------------
    # Concrete no-op defaults: a renderer without a measured-peak gate
    # (Blender) takes no lock and creates nothing.  The view or serializer
    # that writes opens ``transaction.atomic()``, calls ``lock_for_write``
    # first, then writes, then the hook, all in that one block.  Hooks
    # never raise a DRF exception.

    def lock_for_write(self, project):
        """Lock and re-read ``project`` before a write that can fire a hook.

        Returns the locked row, or ``None`` when the renderer takes no lock.
        """
        return None

    def locked_write(self, project):
        """Context manager around a write that can fire a hook (FR-PM-10).

        Renderers that lock open ``transaction.atomic()`` and run
        ``lock_for_write`` first, yielding the locked row.  The default
        opens no transaction and yields ``None``, so such a write (Blender:
        a ``.blend`` upload) takes no new transaction or lock (FR-PM-8).
        """
        return nullcontext(None)

    def after_job_create(self, job):
        """Run after a job of this renderer was inserted (FR-PM-2 trigger a)."""
        return None

    def after_job_update(self, job, changed):
        """Run after a job update; ``changed`` names the fields sent (trigger a')."""
        return None

    def after_asset_create(self, asset):
        """Run after an asset of this renderer was inserted (trigger b)."""
        return None

    def after_project_update(self, project, changed):
        """Run after a project update; ``changed`` names the fields sent (trigger c)."""
        return None

    def project_measurement(self, project, context):
        """Return the project's ``unreal_measurement`` summary, or ``None`` (FR-PM-7)."""
        return None
