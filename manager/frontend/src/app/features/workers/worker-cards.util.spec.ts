// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { CARD_OUTCOME_CODES, CardEntry, CardState, CardStatus } from '../../core/services/worker-cards.model';
import {
  actionErrorText, addressLabel, alertCount, brandNote, cardBrand, cardName, gpuColumnText, hasCardStatus, heldText,
  noticeText, oneFamily, screenLabels, shortAddress, stateLabel, stateTone, validTime,
} from './worker-cards.util';
import { makeAlert, makeCard, makeStatus } from './worker-cards.fixtures';

describe('worker-cards.util', () => {
  describe('hasCardStatus', () => {
    it('is false for null, undefined and {}', () => {
      expect(hasCardStatus(null)).toBeFalse();
      expect(hasCardStatus(undefined)).toBeFalse();
      expect(hasCardStatus({})).toBeFalse();
    });

    it('is true only for schema 1', () => {
      expect(hasCardStatus(makeStatus())).toBeTrue();
      expect(hasCardStatus(makeStatus({ schema: 2 }))).toBeFalse();
    });
  });

  it('counts alerts only of a usable report', () => {
    expect(alertCount(makeStatus({ alerts: [makeAlert(), makeAlert({ id: 'c'.repeat(32) })] }))).toBe(2);
    expect(alertCount({ alerts: [makeAlert()] } as CardStatus)).toBe(0);
    expect(alertCount(null)).toBe(0);
  });

  describe('addresses and names', () => {
    it('gives bus:device.function of a pci key', () => {
      expect(shortAddress('pci:0000:01:00.0')).toBe('01:00.0');
      expect(shortAddress('pci:10000:79:00.1')).toBe('79:00.1');
      expect(addressLabel('pci:0000:23:00.0')).toBe('PCI 23:00.0');
    });

    it('gives no address for apple-gpu or a bad key', () => {
      expect(shortAddress('apple-gpu')).toBeNull();
      expect(addressLabel('pci:zz')).toBe('');
    });

    it('falls back from name to address to a generic name', () => {
      expect(cardName({ key: 'pci:0000:01:00.0', name: 'RX 7900 XT' })).toBe('RX 7900 XT');
      expect(cardName({ key: 'pci:0000:01:00.0' })).toBe('PCI 01:00.0');
      expect(cardName({ key: 'apple-gpu' })).toBe('Graphics card');
    });
  });

  describe('labels', () => {
    it('labels every state', () => {
      const states: CardState[] = ['ok', 'lost', 'changed', 'blender_lost', 'held', 'unticked', 'unknown'];
      for (const s of states) expect(stateLabel(s)).not.toBe('Unknown state');
      expect(stateTone('lost')).toBe('bad');
      expect(stateTone('blender_lost')).toBe('amber');
    });

    it('never returns an inherited member for an odd state', () => {
      expect(stateLabel('toString' as CardState)).toBe('Unknown state');
      expect(stateTone('constructor' as CardState)).toBe('plain');
    });

    it('builds notice texts', () => {
      expect(noticeText({ kind: 'card_new', key: 'pci:0000:02:00.0', name: 'RX 7800 XT' }))
        .toBe('New card found: RX 7800 XT');
      expect(noticeText({ kind: 'card_gone', key: 'pci:0000:02:00.0' }))
        .toBe('Card no longer present: PCI 02:00.0');
    });
  });

  describe('screenLabels', () => {
    it('marks a card that drives the main screen', () => {
      expect(screenLabels({ screen: true, primary_screen: true })).toEqual(['Drives a screen', 'Main screen']);
    });

    it('marks a card that drives another screen', () => {
      expect(screenLabels({ screen: true, primary_screen: false })).toEqual(['Drives a screen']);
    });

    it('marks a screen with an unknown main screen (Linux)', () => {
      expect(screenLabels({ screen: true, primary_screen: null })).toEqual(['Drives a screen']);
    });

    it('says unknown for null, absent and odd values', () => {
      expect(screenLabels({ screen: null, primary_screen: null })).toEqual(['Screen: unknown']);
      expect(screenLabels({ screen: null, primary_screen: true })).toEqual(['Screen: unknown']);
      expect(screenLabels({})).toEqual(['Screen: unknown']);
      expect(screenLabels({ screen: 'yes' } as unknown as CardEntry)).toEqual(['Screen: unknown']);
    });

    it('shows nothing for a card without a screen', () => {
      expect(screenLabels({ screen: false, primary_screen: false })).toEqual([]);
      expect(screenLabels({ screen: false, primary_screen: true })).toEqual([]);
    });
  });

  describe('gpuColumnText', () => {
    it('shows the ticked cards when the report has cards', () => {
      const status = makeStatus({ cards: [
        makeCard({ name: 'RTX 5090' }),
        makeCard({ key: 'pci:0000:02:00.0', name: 'RTX 5080', ticked: false, state: 'unticked' }),
        makeCard({ key: 'pci:0000:03:00.0', name: 'RTX 5070 Ti' }),
      ] });
      expect(gpuColumnText(status, 'old')).toBe('RTX 5090, RTX 5070 Ti');
    });

    it('says no card is ticked when none is', () => {
      const status = makeStatus({ cards: [makeCard({ ticked: false, state: 'unticked' })] });
      expect(gpuColumnText(status, 'old')).toBe('No card ticked');
    });

    it('falls back to gpu_name without cards', () => {
      expect(gpuColumnText(null, 'old')).toBe('old');
      expect(gpuColumnText({}, 'old')).toBe('old');
      expect(gpuColumnText(makeStatus({ cards: [] }), 'old')).toBe('old');
    });
  });

  it('applies the one-family rule', () => {
    expect(oneFamily([{ backend: 'CUDA' }, { backend: 'OPTIX' }])).toBeTrue();
    expect(oneFamily([{ backend: 'HIP' }, { backend: 'HIP' }, { backend: null }])).toBeTrue();
    expect(oneFamily([{ backend: 'HIP' }, { backend: null }])).toBeFalse();
    expect(oneFamily([{ backend: 'HIP' }, { backend: 'CUDA' }])).toBeFalse();
    expect(oneFamily([])).toBeFalse();
  });

  it('accepts only parseable times', () => {
    expect(validTime('2026-11-19T10:00:00+00:00')).toBe('2026-11-19T10:00:00+00:00');
    expect(validTime(null)).toBeNull();
    expect(validTime('')).toBeNull();
    expect(validTime('not a date')).toBeNull();
  });

  it('uses the detail of an error answer, else the HTTP status', () => {
    expect(actionErrorText(400, { detail: 'Unknown alert.' })).toBe('Unknown alert.');
    expect(actionErrorText(500, { detail: '' })).toBe('The request failed (HTTP 500).');
    expect(actionErrorText(502, '<html>bad gateway</html>')).toBe('The request failed (HTTP 502).');
    expect(actionErrorText(0, null)).toBe('The request failed (HTTP 0).');
  });

  it('cardBrand names the PCI vendor, else the backend family, else null (D7)', () => {
    expect(cardBrand({ vendor_id: 0x10de, backend: null })).toBe('NVIDIA');
    expect(cardBrand({ vendor_id: 0x1002, backend: 'CUDA' })).toBe('AMD');
    expect(cardBrand({ vendor_id: 0x8086 })).toBe('Intel');
    expect(cardBrand({ vendor_id: 0x1af4, backend: 'HIP' })).toBe('PCI 1af4');
    expect(cardBrand({ vendor_id: 0x5, backend: null })).toBe('PCI 0005');
    const families = { CUDA: 'NVIDIA', OPTIX: 'NVIDIA', HIP: 'AMD', ONEAPI: 'Intel', METAL: 'Apple' } as const;
    for (const [backend, brand] of Object.entries(families)) {
      expect(cardBrand({ vendor_id: null, backend: backend as keyof typeof families })).withContext(backend).toBe(brand);
    }
    expect(cardBrand({ vendor_id: null, backend: null })).toBeNull();
    expect(cardBrand({})).toBeNull();
    expect(cardBrand({ vendor_id: 1.5, backend: 'toString' as never })).toBeNull();
    expect(cardBrand({ vendor_id: 0x10000, backend: null })).toBeNull();
  });

  it('brandNote words the note as the worker pages do (FR-PC-BR-5)', () => {
    expect(brandNote('AMD', [])).toBeNull();
    expect(brandNote(null, [])).toBeNull();
    expect(brandNote('NVIDIA', ['NVIDIA', 'NVIDIA'])).toBeNull();
    expect(brandNote('AMD', ['NVIDIA'])).toBe('Not supported together with NVIDIA cards.');
    expect(brandNote('NVIDIA', ['NVIDIA', 'PCI 1af4'])).toBe('Not supported together with PCI 1af4 cards.');
    expect(brandNote(null, ['NVIDIA'])).toBe('Not supported together with other cards.');
    expect(brandNote('NVIDIA', [null])).toBe('Not supported together with other cards.');
    expect(brandNote('NVIDIA', ['AMD', null])).toBe('Not supported together with other cards.');
  });

  it('heldText names the holding job and its renderer, null when free or invalid (FR-PC-MGR-4)', () => {
    expect(heldText({ job_id: 12, renderer: 'BLENDER' })).toBe('Rendering job 12 (Blender)');
    expect(heldText({ job_id: 7, renderer: 'UNREAL' })).toBe('Rendering job 7 (Unreal)');
    expect(heldText({ job_id: null, renderer: null })).toBeNull();
    expect(heldText({})).toBeNull();
    expect(heldText({ job_id: 12, renderer: null })).toBeNull();
    expect(heldText({ job_id: 0, renderer: 'UNREAL' })).toBeNull();
    expect(heldText({ job_id: 1.5, renderer: 'UNREAL' })).toBeNull();
    expect(heldText({ job_id: 3, renderer: 'toString' as unknown as 'UNREAL' })).toBeNull();
  });

  it('lists the outcome codes of the manager, mixed_brands included (FR-PC-BR-5)', () => {
    expect([...CARD_OUTCOME_CODES]).toEqual([
      'applied', 'invalid', 'env_override', 'not_allowed', 'probe_failed',
      'identity_mismatch', 'mixed_brands', 'stale_revision', 'card_held', 'save_failed',
    ]);
  });
});
