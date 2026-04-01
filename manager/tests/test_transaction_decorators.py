# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests verifying that perform_create methods on AnimationViewSet
and TiledJobViewSet are wrapped with @transaction.atomic.

These tests use introspection to confirm the decorator is present,
ensuring that child job creation is rolled back if any step fails.
"""

from django.db import transaction

from workers.views.animations import AnimationViewSet
from workers.views.tiled_jobs import TiledJobViewSet


def _is_wrapped_with_atomic(method):
    """
    Check whether a method is wrapped with @transaction.atomic.

    Django's @transaction.atomic decorator wraps the function and stores
    the original in __wrapped__. It also sets a `_non_atomic_requests`
    attribute on certain paths. We check multiple indicators:
    1. The __wrapped__ attribute exists (functools.wraps used by atomic)
    2. The wrapper is an instance of transaction.Atomic
    """
    # Django's @transaction.atomic as a decorator produces an Atomic instance
    # when used as a context manager, but when used as a decorator it wraps
    # the function. The wrapped function will have __wrapped__ pointing to
    # the original.
    if hasattr(method, '__wrapped__'):
        return True

    # Alternative check: look at the closure or the class of the wrapper
    # Django's atomic decorator creates an Atomic object that is callable
    if isinstance(method, transaction.Atomic):
        return True

    return False


class TestAnimationViewSetTransactionWrapping:
    """Tests that AnimationViewSet.perform_create uses @transaction.atomic."""

    def test_perform_create_is_atomic(self):
        """
        AnimationViewSet.perform_create must be decorated with
        @transaction.atomic to ensure all child Job creation rolls
        back on failure.
        """
        method = AnimationViewSet.perform_create
        assert _is_wrapped_with_atomic(method), (
            "AnimationViewSet.perform_create is not wrapped with "
            "@transaction.atomic"
        )

    def test_perform_create_has_correct_signature(self):
        """
        Verify perform_create accepts self and serializer arguments,
        ensuring the decorator hasn't mangled the method signature.
        """
        import inspect
        sig = inspect.signature(AnimationViewSet.perform_create)
        params = list(sig.parameters.keys())
        assert 'self' in params
        assert 'serializer' in params


class TestTiledJobViewSetTransactionWrapping:
    """Tests that TiledJobViewSet.perform_create uses @transaction.atomic."""

    def test_perform_create_is_atomic(self):
        """
        TiledJobViewSet.perform_create must be decorated with
        @transaction.atomic to ensure all child Job creation rolls
        back on failure.
        """
        method = TiledJobViewSet.perform_create
        assert _is_wrapped_with_atomic(method), (
            "TiledJobViewSet.perform_create is not wrapped with "
            "@transaction.atomic"
        )

    def test_perform_create_has_correct_signature(self):
        """
        Verify perform_create accepts self and serializer arguments.
        """
        import inspect
        sig = inspect.signature(TiledJobViewSet.perform_create)
        params = list(sig.parameters.keys())
        assert 'self' in params
        assert 'serializer' in params
