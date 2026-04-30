# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Coverage expansion: ``wizard/sethlans_wizard/checkpoints.py``
(FR-CHK2 / FR-CHK3 — checkpoint name constants + RESUME_NEXT_ROUTE).

The dev agent's smoke pass asserted basic import wiring; this module
locks the contract. Each named constant and the resume-route mapping
is reviewer-blessed copy that must not drift, and the tests below
fail loudly if a rename or accidental edit slips through.
"""

from __future__ import annotations

from wizard.sethlans_wizard import checkpoints


class TestCheckpointNames:

    def test_individual_constants_match_spec(self):
        # FR-CHK2 — the literal names are part of the spec contract;
        # any rename must be an intentional spec change.
        assert checkpoints.WELCOME_SEEN == "welcome_seen"
        assert checkpoints.TOPOLOGY_CHOSEN == "topology_chosen"
        assert checkpoints.NETWORK_CONFIGURED == "network_configured"
        assert checkpoints.DATABASE_CONFIGURED == "database_configured"
        assert checkpoints.ADMIN_VALIDATED == "admin_validated"
        assert checkpoints.WORKER_PASSWORD_SET == "worker_password_set"
        assert checkpoints.VERIFIED == "verified"

    def test_checkpoint_names_tuple_in_canonical_order(self):
        # Order matters — the resume walker iterates this tuple to find
        # the first un-recorded checkpoint.
        assert checkpoints.CHECKPOINT_NAMES == (
            "welcome_seen",
            "topology_chosen",
            "network_configured",
            "database_configured",
            "admin_validated",
            "worker_password_set",
            "verified",
        )

    def test_checkpoint_names_is_immutable_tuple(self):
        # An accidental list-vs-tuple change would let mutations leak
        # into the global; pin the type explicitly.
        assert isinstance(checkpoints.CHECKPOINT_NAMES, tuple)

    def test_no_duplicate_checkpoint_names(self):
        names = checkpoints.CHECKPOINT_NAMES
        assert len(set(names)) == len(names)


class TestManagerWorkerOnlyCheckpoints:

    def test_contains_worker_password_set(self):
        # FR-CHK2 — only manager_worker emits worker_password_set.
        assert checkpoints.WORKER_PASSWORD_SET in (
            checkpoints.MANAGER_WORKER_ONLY_CHECKPOINTS
        )

    def test_is_frozenset(self):
        assert isinstance(
            checkpoints.MANAGER_WORKER_ONLY_CHECKPOINTS, frozenset,
        )

    def test_no_other_checkpoints_marked_manager_worker_only(self):
        # The manager topology MUST be able to reach VERIFIED with all
        # entries except WORKER_PASSWORD_SET.
        assert checkpoints.MANAGER_WORKER_ONLY_CHECKPOINTS == frozenset(
            {checkpoints.WORKER_PASSWORD_SET},
        )


class TestResumeNextRoute:

    def test_every_checkpoint_has_a_next_route(self):
        # FR-CHK3 — the wizard's re-auth flow must be able to compute a
        # next route for every recorded checkpoint.
        for name in checkpoints.CHECKPOINT_NAMES:
            assert name in checkpoints.RESUME_NEXT_ROUTE, (
                f"Missing next-route mapping for {name!r}"
            )

    def test_specific_mappings(self):
        # FR-CHK3 — these are the routes Phase 2's frontend will navigate
        # the user back to. Pin them so a frontend-side rewrite doesn't
        # silently break resume.
        m = checkpoints.RESUME_NEXT_ROUTE
        assert m["welcome_seen"] == "/topology"
        assert m["topology_chosen"] == "/network"
        assert m["network_configured"] == "/database"
        assert m["database_configured"] == "/admin-user"
        assert m["admin_validated"] == "/worker-password"
        assert m["worker_password_set"] == "/verify"
        assert m["verified"] == "/done"

    def test_all_routes_are_absolute(self):
        # The frontend's resume-redirect pushes whatever route lands
        # here directly into ``location.assign`` — relative paths would
        # produce undefined navigation.
        for route in checkpoints.RESUME_NEXT_ROUTE.values():
            assert route.startswith("/"), route


class TestResumeWalkerSemantics:
    """Coverage expansion: simulate the resume walker walking
    CHECKPOINT_NAMES + RESUME_NEXT_ROUTE to find the first incomplete
    step. Phase 2 owns the actual resume endpoint; this fixes the
    contract Phase 1 emits.
    """

    @staticmethod
    def _next_route(recorded: list[str]) -> str:
        """Walk CHECKPOINT_NAMES; the first not-yet-recorded one is
        the resume target. If everything is recorded, return /done."""
        recorded_set = set(recorded)
        for name in checkpoints.CHECKPOINT_NAMES:
            if name not in recorded_set:
                # Map the LAST recorded checkpoint to its next route.
                if not recorded:
                    return "/welcome"
                latest = recorded[-1]
                return checkpoints.RESUME_NEXT_ROUTE[latest]
        return "/done"

    def test_empty_progress_resumes_at_welcome(self):
        assert self._next_route([]) == "/welcome"

    def test_after_topology_resumes_at_network(self):
        assert self._next_route(
            ["welcome_seen", "topology_chosen"],
        ) == "/network"

    def test_all_complete_walks_to_done(self):
        assert self._next_route(
            list(checkpoints.CHECKPOINT_NAMES),
        ) == "/done"


class TestExports:

    def test_dunder_all_lists_every_public_name(self):
        for name in (
            "WELCOME_SEEN",
            "TOPOLOGY_CHOSEN",
            "NETWORK_CONFIGURED",
            "DATABASE_CONFIGURED",
            "ADMIN_VALIDATED",
            "WORKER_PASSWORD_SET",
            "VERIFIED",
            "CHECKPOINT_NAMES",
            "MANAGER_WORKER_ONLY_CHECKPOINTS",
            "RESUME_NEXT_ROUTE",
        ):
            assert name in checkpoints.__all__
