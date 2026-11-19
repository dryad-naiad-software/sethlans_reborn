// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { CardChoiceOutcome, CardChoiceRequest, CardStatus } from '../../core/services/worker-cards.model';
import {
  READ_ONLY_ENV, READ_ONLY_NO_STATUS, READ_ONLY_NOT_ALLOWED, ageText, buildChoiceBody, canNewlyTick,
  hasOutcome, hasPending, outcomeLine, readOnlyReason, withdrawnEnds, revisionOf, shownCards, takeSnapshot,
} from './worker-card-choice.util';
import { makeAlert, makeCard, makeOutcome, makeRequest, makeStatus } from './worker-cards.fixtures';

const KEY1 = 'pci:0000:01:00.0';
const KEY2 = 'pci:0000:02:00.0';
const KEY3 = 'pci:0000:03:00.0';

describe('worker card choice helpers', () => {
  it('hasPending and hasOutcome treat null, undefined and {} as absent (R3)', () => {
    for (const absent of [null, undefined, {} as CardChoiceRequest]) {
      expect(hasPending(absent)).toBeFalse();
    }
    for (const absent of [null, undefined, {} as CardChoiceOutcome]) {
      expect(hasOutcome(absent)).toBeFalse();
    }
    expect(hasPending(makeRequest())).toBeTrue();
    expect(hasOutcome(makeOutcome())).toBeTrue();
    expect(hasOutcome(makeOutcome({ result: 'refused', code: 'card_held' }))).toBeTrue();
  });

  it('readOnlyReason gives the FR-CF-7 sentences in order', () => {
    expect(readOnlyReason(null)).toBe(READ_ONLY_NO_STATUS);
    expect(readOnlyReason({} as CardStatus)).toBe(READ_ONLY_NO_STATUS);
    expect(readOnlyReason(makeStatus({ schema: 2 }))).toBe(READ_ONLY_NO_STATUS);
    expect(readOnlyReason(makeStatus({ env_override: true, manager_changes_allowed: false })))
      .toBe(READ_ONLY_ENV);
    expect(readOnlyReason(makeStatus({ manager_changes_allowed: false }))).toBe(READ_ONLY_NOT_ALLOWED);
    expect(readOnlyReason(makeStatus({ manager_changes_allowed: undefined }))).toBe(READ_ONLY_NOT_ALLOWED);
    expect(readOnlyReason(makeStatus())).toBeNull();
  });

  it('takes a snapshot of the cards, the holding alerts, method, mode and revision', () => {
    const snapshot = takeSnapshot(makeStatus({
      method: 'cpu_gpu', gpu_mode: 'combined', revision: 9,
      cards: [makeCard(), makeCard({ key: KEY2, name: '', vendor_id: undefined, backend: undefined })],
      alerts: [
        makeAlert({ kind: 'card_lost_blender', key: KEY2 }),
        makeAlert({ id: 'c'.repeat(32), kind: 'adapter_mismatch', key: KEY1 }),
      ],
    }));
    expect(snapshot.method).toBe('cpu_gpu');
    expect(snapshot.gpuMode).toBe('combined');
    expect(snapshot.revision).toBe(9);
    expect(snapshot.holding).toEqual([KEY2]);
    expect(snapshot.cards[1]).toEqual(jasmine.objectContaining({
      key: KEY2, name: 'PCI 02:00.0', vendor_id: null, backend: null, ticked: true,
    }));
  });

  it('snapshots an absent report as CPU, split, revision 0 and no cards', () => {
    const snapshot = takeSnapshot(null);
    expect(snapshot).toEqual({ method: 'cpu', gpuMode: 'split', revision: 0, cards: [], holding: [] });
    expect(revisionOf({} as CardStatus)).toBe(0);
  });

  it('lists every card that is not lost', () => {
    const snapshot = takeSnapshot(makeStatus({ cards: [
      makeCard(), makeCard({ key: KEY2, state: 'lost' }), makeCard({ key: KEY3, state: 'unknown' }),
    ] }));
    expect(shownCards(snapshot).map(c => c.key)).toEqual([KEY1, KEY3]);
  });

  it('canNewlyTick blocks an unticked held, changed, blender_lost or alerted card only', () => {
    const snapshot = takeSnapshot(makeStatus({
      cards: [
        makeCard({ key: KEY1, ticked: false, state: 'unticked' }),
        makeCard({ key: KEY2, ticked: false, state: 'unticked' }),
        makeCard({ key: KEY3, ticked: true, state: 'held' }),
      ],
      alerts: [makeAlert({ kind: 'card_changed', key: KEY2 })],
    }));
    const [free, alerted, keptHeld] = snapshot.cards;
    expect(canNewlyTick(free, snapshot.holding)).toBeTrue();
    expect(canNewlyTick(alerted, snapshot.holding)).toBeFalse();
    expect(canNewlyTick(keptHeld, snapshot.holding)).toBeTrue();
    for (const state of ['changed', 'held', 'blender_lost'] as const) {
      expect(canNewlyTick({ ...free, state }, [])).withContext(state).toBeFalse();
    }
    expect(canNewlyTick({ ...free, state: 'unknown' }, [])).toBeTrue();
  });

  describe('buildChoiceBody', () => {
    const snapshot = takeSnapshot(makeStatus({
      revision: 7,
      cards: [
        makeCard({ key: KEY1, backend: 'CUDA' }),
        makeCard({ key: KEY2, backend: 'OPTIX', vendor_id: 0x10de, device_id: 0x2684 }),
        makeCard({ key: KEY3, state: 'lost' }),
      ],
    }));

    it('names the ticked cards with the snapshot ids, the shown keys and the snapshot revision', () => {
      expect(buildChoiceBody(snapshot, 'gpu', true, new Set([KEY1, KEY2, KEY3]))).toEqual({
        method: 'gpu', gpu_mode: 'combined',
        cards: [
          { key: KEY1, vendor_id: 0x10de, device_id: 0x2b85 },
          { key: KEY2, vendor_id: 0x10de, device_id: 0x2684 },
        ],
        shown: [KEY1, KEY2], base_revision: 7,
      });
    });

    it('sends split with fewer than two ticked cards (R4)', () => {
      const body = buildChoiceBody(snapshot, 'gpu', true, new Set([KEY1]));
      expect(body.gpu_mode).toBe('split');
      expect(body.cards.length).toBe(1);
    });

    it('never sends combined when the method is CPU (C-23)', () => {
      expect(buildChoiceBody(snapshot, 'cpu', true, new Set([KEY1, KEY2])).gpu_mode).toBe('split');
      expect(buildChoiceBody(snapshot, 'cpu_gpu', true, new Set([KEY1, KEY2])).gpu_mode).toBe('combined');
    });

    it('sends split when the ticked cards are not of one family', () => {
      const mixed = takeSnapshot(makeStatus({ cards: [
        makeCard({ key: KEY1, backend: 'CUDA' }), makeCard({ key: KEY2, backend: null }),
      ] }));
      expect(buildChoiceBody(mixed, 'gpu', true, new Set([KEY1, KEY2])).gpu_mode).toBe('split');
    });
  });

  it('outcomeLine words applied and refused outcomes', () => {
    expect(outcomeLine(makeOutcome(), ' at T')).toBe('Applied at T');
    expect(outcomeLine(makeOutcome({ result: 'refused', code: 'invalid', reason: 'bad' }), '')).toBe('Refused: bad');
    expect(outcomeLine(makeOutcome({ result: 'refused', code: 'invalid', reason: '' }), ' at T')).toBe('Refused at T');
  });

  it('withdrawnEnds only for another request or a new outcome', () => {
    const mark = { requestId: 'd'.repeat(32), outcomeId: 'e'.repeat(32) };
    const row = (request: CardChoiceRequest | null, outcome: CardChoiceOutcome | null) =>
      ({ card_choice_request: request, card_choice_outcome: outcome });
    expect(withdrawnEnds(mark, row(makeRequest(), makeOutcome()))).toBeFalse();
    expect(withdrawnEnds(mark, row(null, makeOutcome()))).toBeFalse();
    expect(withdrawnEnds(mark, row(makeRequest({ id: '9'.repeat(32) }), makeOutcome()))).toBeTrue();
    expect(withdrawnEnds(mark, row(null, makeOutcome({ id: 'f'.repeat(32) })))).toBeTrue();
  });

  it('ageText words the time since a request', () => {
    const start = Date.parse('2026-11-19T10:00:00Z');
    const at = '2026-11-19T10:00:00Z';
    expect(ageText(at, start + 30_000)).toBe('less than a minute');
    expect(ageText(at, start - 5_000)).toBe('less than a minute');
    expect(ageText(at, start + 60_000)).toBe('1 minute');
    expect(ageText(at, start + 5 * 60_000)).toBe('5 minutes');
    expect(ageText(at, start + 2 * 3_600_000)).toBe('2 hours');
    expect(ageText(at, start + 3 * 86_400_000)).toBe('3 days');
    expect(ageText('not a time', start)).toBe('some time');
  });
});
