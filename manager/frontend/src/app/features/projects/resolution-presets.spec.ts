// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  RESOLUTION_PRESETS,
  DEFAULT_PRESET_ID,
  PRESET_GROUP_LABELS,
  ResolutionGroup,
  findPresetByXY,
} from './resolution-presets';

describe('resolution-presets', () => {
  describe('RESOLUTION_PRESETS catalog', () => {
    it('should contain exactly 13 entries', () => {
      expect(RESOLUTION_PRESETS.length).toBe(13);
    });

    it('should appear in the group order horizontal, cinema, vertical, square (no interleaving)', () => {
      const expectedOrder: ResolutionGroup[] = ['horizontal', 'cinema', 'vertical', 'square'];
      let currentExpectedIndex = 0;
      for (const preset of RESOLUTION_PRESETS) {
        const groupIndex = expectedOrder.indexOf(preset.group);
        expect(groupIndex).toBeGreaterThanOrEqual(currentExpectedIndex);
        currentExpectedIndex = groupIndex;
      }
    });

    it('should have every preset id be unique', () => {
      const ids = RESOLUTION_PRESETS.map(p => p.id);
      const uniqueIds = new Set(ids);
      expect(uniqueIds.size).toBe(ids.length);
    });
  });

  describe('DEFAULT_PRESET_ID', () => {
    it('should equal "fhd1080"', () => {
      expect(DEFAULT_PRESET_ID).toBe('fhd1080');
    });

    it('should refer to a preset whose x is 1920 and y is 1080', () => {
      const defaultPreset = RESOLUTION_PRESETS.find(p => p.id === DEFAULT_PRESET_ID);
      expect(defaultPreset).toBeDefined();
      expect(defaultPreset!.x).toBe(1920);
      expect(defaultPreset!.y).toBe(1080);
    });
  });

  describe('findPresetByXY', () => {
    it('should return the fhd1080 preset object for 1920x1080', () => {
      const expected = RESOLUTION_PRESETS.find(p => p.id === 'fhd1080');
      expect(findPresetByXY(1920, 1080)).toEqual(expected!);
    });

    it('should return the uhd4k preset object for 3840x2160', () => {
      const expected = RESOLUTION_PRESETS.find(p => p.id === 'uhd4k');
      expect(findPresetByXY(3840, 2160)).toEqual(expected!);
    });

    it('should return null for 1600x900 (no matching preset)', () => {
      expect(findPresetByXY(1600, 900)).toBeNull();
    });

    it('should return the uhd8k preset object for 7680x4320 (edge of range)', () => {
      const expected = RESOLUTION_PRESETS.find(p => p.id === 'uhd8k');
      expect(findPresetByXY(7680, 4320)).toEqual(expected!);
    });
  });

  describe('PRESET_GROUP_LABELS', () => {
    it('should label horizontal as "Horizontal (16:9)"', () => {
      expect(PRESET_GROUP_LABELS.horizontal).toBe('Horizontal (16:9)');
    });

    it('should label cinema as "Cinema (DCI)"', () => {
      expect(PRESET_GROUP_LABELS.cinema).toBe('Cinema (DCI)');
    });

    it('should label vertical as "Vertical (9:16)"', () => {
      expect(PRESET_GROUP_LABELS.vertical).toBe('Vertical (9:16)');
    });

    it('should label square as "Square (1:1)"', () => {
      expect(PRESET_GROUP_LABELS.square).toBe('Square (1:1)');
    });
  });
});
