# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``SETHLANS_COMPUTE_CARDS`` naming cards of more than one brand (unreal-render-per-card FR-PC-BR-3, CS FR-CC-2).

The variable names PCI addresses only, so the brands are known once an inventory lists the named cards.
``check(section, inv)`` applies the one-brand rule (``cards.brand.mixed_brands``) to the named cards the
inventory lists; the first time it fails, one ERROR line names the brands and the finding holds for the life of
the process (the environment does not change while the worker runs): ``config_compute.parse`` then treats the
variable as an invalid value (``method = cpu``), ``applied`` does the same to a section read before the
finding, and ``cards.unreal_cards`` disables Unreal with ``MIXED_ENV_REASON``.

``_lock`` is a leaf lock guarding ``_mixed``.
"""
import dataclasses
import logging
import threading
from typing import Optional, Tuple

from sethlans_worker_agent.cards.brand import mixed_brands

logger = logging.getLogger(__name__)

ENV_CARDS = 'SETHLANS_COMPUTE_CARDS'  # config_compute.ENV_CARDS (config_compute imports this module)
MIXED_ENV_REASON = "graphics cards of more than one brand are ticked (not supported)"

_lock = threading.Lock()  # leaf
_mixed: Optional[Tuple[str, ...]] = None  # the brand labels once the named cards broke the rule; under _lock


def seen_mixed() -> Optional[Tuple[str, ...]]:
    """The brand labels once the cards named by the variable were found to be of more than one brand, else ``None``."""
    with _lock:
        return _mixed


def check(section, inv=None) -> Optional[Tuple[str, ...]]:
    """For a section whose cards come from the variable: the brand labels once the rule failed (now or before).

    ``inv`` (optional) is an inventory to apply the rule to; ``None`` for any other section.
    """
    global _mixed
    if not getattr(section, 'env_cards', False):
        return None
    labels = None
    if inv is not None:
        by_key = {card.key: card for card in inv.cards}
        labels = mixed_brands(by_key[key] for key in section.ticked_keys if key in by_key)
    with _lock:
        newly = labels is not None and _mixed is None
        if newly:
            _mixed = labels
        found = _mixed
    if newly:
        logger.error("%s names graphics cards of more than one brand (%s); this is not supported. This worker "
                     "renders on the CPU only and Unreal is off until it is fixed.", ENV_CARDS, ', '.join(labels))
    return found


def applied(section, inv=None):
    """``section`` with ``method = cpu`` and the variable marked invalid when ``check`` finds mixed brands."""
    if check(section, inv) is None or (section.method == 'cpu' and ENV_CARDS in section.invalid):
        return section
    invalid = section.invalid if ENV_CARDS in section.invalid else section.invalid + (ENV_CARDS,)
    return dataclasses.replace(section, method='cpu', invalid=invalid)


def reset_for_tests() -> None:
    """Forget the finding. Test-only hook."""
    global _mixed
    with _lock:
        _mixed = None
