// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  generateOutputFilePattern,
  buildRenderSettings,
  buildTiledRenderSettings,
  parseTilingConfig,
  RENDER_ENGINES,
  RENDER_DEVICES,
  TILING_OPTIONS,
  ANIMATION_TILING_OPTIONS,
  OUTPUT_FORMATS,
} from './render-payload.util';

describe('render-payload.util', () => {

  describe('generateOutputFilePattern', () => {
    it('should convert a simple name to a slug pattern', () => {
      expect(generateOutputFilePattern('My Render'))
        .toBe('//render/my_render_####.png');
    });

    it('should handle names with multiple spaces', () => {
      expect(generateOutputFilePattern('A  Long   Name'))
        .toBe('//render/a_long_name_####.png');
    });

    it('should handle names with special characters', () => {
      expect(generateOutputFilePattern('Test @#$ Render!'))
        .toBe('//render/test_render_####.png');
    });

    it('should handle names with leading/trailing special chars', () => {
      expect(generateOutputFilePattern('---hello---'))
        .toBe('//render/hello_####.png');
    });

    it('should handle single word names', () => {
      expect(generateOutputFilePattern('render'))
        .toBe('//render/render_####.png');
    });

    it('should handle names with numbers', () => {
      expect(generateOutputFilePattern('Scene 42 Final'))
        .toBe('//render/scene_42_final_####.png');
    });

    it('should return pattern with empty slug for empty string', () => {
      expect(generateOutputFilePattern('')).toBe('//render/_####.png');
    });

    it('should handle uppercase conversion', () => {
      expect(generateOutputFilePattern('MY RENDER'))
        .toBe('//render/my_render_####.png');
    });
  });

  describe('buildRenderSettings', () => {
    it('should include samples, resolution_x, and resolution_y', () => {
      const result = buildRenderSettings(128, 1920, 1080);
      expect(result).toEqual({
        'cycles.samples': 128,
        'render.resolution_x': 1920,
        'render.resolution_y': 1080,
      });
    });

    it('should handle small values', () => {
      const result = buildRenderSettings(1, 1, 1);
      expect(result).toEqual({
        'cycles.samples': 1,
        'render.resolution_x': 1,
        'render.resolution_y': 1,
      });
    });
  });

  describe('buildTiledRenderSettings', () => {
    it('should only include samples (no resolution)', () => {
      const result = buildTiledRenderSettings(256);
      expect(result).toEqual({ 'cycles.samples': 256 });
    });

    it('should not include resolution keys', () => {
      const result = buildTiledRenderSettings(128);
      expect(result['render.resolution_x']).toBeUndefined();
      expect(result['render.resolution_y']).toBeUndefined();
    });
  });

  describe('parseTilingConfig', () => {
    it('should parse 2x2', () => {
      expect(parseTilingConfig('2x2'))
        .toEqual({ tile_count_x: 2, tile_count_y: 2 });
    });

    it('should parse 3x3', () => {
      expect(parseTilingConfig('3x3'))
        .toEqual({ tile_count_x: 3, tile_count_y: 3 });
    });

    it('should parse 4x4', () => {
      expect(parseTilingConfig('4x4'))
        .toEqual({ tile_count_x: 4, tile_count_y: 4 });
    });

    it('should parse 5x5', () => {
      expect(parseTilingConfig('5x5'))
        .toEqual({ tile_count_x: 5, tile_count_y: 5 });
    });

    it('should default to 2x2 for invalid input', () => {
      expect(parseTilingConfig('invalid'))
        .toEqual({ tile_count_x: 2, tile_count_y: 2 });
    });

    it('should default to 2x2 for empty string', () => {
      expect(parseTilingConfig(''))
        .toEqual({ tile_count_x: 2, tile_count_y: 2 });
    });
  });

  describe('constant arrays', () => {
    it('should have three render engines', () => {
      expect(RENDER_ENGINES.length).toBe(3);
      expect(RENDER_ENGINES.map(e => e.value))
        .toEqual(['CYCLES', 'BLENDER_EEVEE_NEXT', 'WORKBENCH']);
    });

    it('should have three render devices', () => {
      expect(RENDER_DEVICES.length).toBe(3);
      expect(RENDER_DEVICES.map(d => d.value))
        .toEqual(['CPU', 'GPU', 'ANY']);
    });

    it('should have tiling options without NONE', () => {
      expect(TILING_OPTIONS.map(t => t.value))
        .toEqual(['2x2', '3x3', '4x4', '5x5']);
    });

    it('should have animation tiling options with NONE first', () => {
      expect(ANIMATION_TILING_OPTIONS[0].value).toBe('NONE');
      expect(ANIMATION_TILING_OPTIONS.length).toBe(5);
    });

    it('should have PNG as the only output format', () => {
      expect(OUTPUT_FORMATS).toEqual([{ value: 'PNG', label: 'PNG' }]);
    });
  });
});
