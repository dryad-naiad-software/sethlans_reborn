# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Contract test: the Angular ``CARD_OUTCOME_CODES`` list in
``core/services/worker-cards.model.ts`` matches the manager's
``CARD_OUTCOME_CODES`` tuple, in order (``mixed_brands`` included).
"""

from __future__ import annotations

import re
from pathlib import Path

from workers.serializers.worker_card_schemas import CARD_OUTCOME_CODES

MODEL_TS = (Path(__file__).resolve().parents[3]
            / 'manager' / 'frontend' / 'src' / 'app' / 'core' / 'services' / 'worker-cards.model.ts')


def _ts_codes() -> tuple[str, ...]:
    text = MODEL_TS.read_text(encoding='utf-8')
    match = re.search(r'export const CARD_OUTCOME_CODES = \[(.*?)\] as const;', text, re.S)
    assert match, 'CARD_OUTCOME_CODES not found in worker-cards.model.ts'
    return tuple(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_outcome_codes_match_the_manager():
    assert _ts_codes() == CARD_OUTCOME_CODES
    assert 'mixed_brands' in _ts_codes()
