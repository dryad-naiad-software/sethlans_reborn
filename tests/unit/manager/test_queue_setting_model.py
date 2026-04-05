# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the QueueSetting singleton model.

Covers AC-8, AC-22, AC-24: singleton enforcement, get_instance
auto-creation, delete no-op, and default queue_paused=False.
"""

import pytest

from workers.models.queue_setting import QueueSetting


@pytest.mark.django_db
class TestQueueSettingGetInstance:
    """Tests for QueueSetting.get_instance() — AC-22."""

    def test_creates_if_not_exists(self):
        """get_instance creates the singleton row when table is empty."""
        assert QueueSetting.objects.count() == 0
        instance = QueueSetting.get_instance()
        assert instance.pk == 1
        assert QueueSetting.objects.count() == 1

    def test_returns_existing_instance(self):
        """Calling get_instance twice returns the same row."""
        first = QueueSetting.get_instance()
        second = QueueSetting.get_instance()
        assert first.pk == second.pk == 1

    def test_default_queue_paused_is_false(self):
        """AC-8: Default queue_paused is False."""
        instance = QueueSetting.get_instance()
        assert instance.queue_paused is False

    def test_persists_queue_paused_true(self):
        """After setting queue_paused=True, get_instance reflects it."""
        instance = QueueSetting.get_instance()
        instance.queue_paused = True
        instance.save()
        refreshed = QueueSetting.get_instance()
        assert refreshed.queue_paused is True


@pytest.mark.django_db
class TestQueueSettingSaveSingleton:
    """Tests for save() always setting pk=1 — AC-22."""

    def test_save_forces_pk_1(self):
        """Creating with a different pk is overridden to pk=1."""
        obj = QueueSetting(pk=99, queue_paused=True)
        obj.save()
        assert obj.pk == 1
        assert QueueSetting.objects.count() == 1

    def test_save_overwrites_existing(self):
        """Saving a new instance with different pk overwrites pk=1."""
        QueueSetting.get_instance()
        obj = QueueSetting(pk=42, queue_paused=True)
        obj.save()
        assert QueueSetting.objects.count() == 1
        assert QueueSetting.objects.get(pk=1).queue_paused is True

    def test_save_without_pk_uses_1(self):
        """A freshly constructed instance gets pk=1 on save."""
        obj = QueueSetting(queue_paused=True)
        obj.save()
        assert obj.pk == 1


@pytest.mark.django_db
class TestQueueSettingDeleteNoOp:
    """Tests for delete() being a no-op — AC-22."""

    def test_delete_instance_is_noop(self):
        """Calling delete() on an instance does not remove the row."""
        instance = QueueSetting.get_instance()
        instance.delete()
        assert QueueSetting.objects.filter(pk=1).exists()

    def test_delete_preserves_data(self):
        """Data is preserved after a delete attempt."""
        instance = QueueSetting.get_instance()
        instance.queue_paused = True
        instance.save()
        instance.delete()
        refreshed = QueueSetting.objects.get(pk=1)
        assert refreshed.queue_paused is True


@pytest.mark.django_db
class TestQueueSettingMeta:
    """Verify model meta configuration."""

    def test_verbose_name(self):
        assert QueueSetting._meta.verbose_name == "Queue Setting"

    def test_verbose_name_plural(self):
        assert QueueSetting._meta.verbose_name_plural == "Queue Settings"
