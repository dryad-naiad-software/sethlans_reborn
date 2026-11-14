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
    def poll_q(self, params):
        """Return the ``Q`` selecting this renderer's jobs for a worker poll."""

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
