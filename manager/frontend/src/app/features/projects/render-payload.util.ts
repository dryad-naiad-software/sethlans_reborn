// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

/** Map output format values to their file extensions. */
export const FORMAT_EXTENSIONS: Record<string, string> = {
  'PNG': '.png', 'JPEG': '.jpg', 'OPEN_EXR': '.exr',
  'OPEN_EXR_MULTILAYER': '.exr', 'TIFF': '.tif',
  'BMP': '.bmp', 'HDR': '.hdr', 'TARGA': '.tga',
};

/**
 * Generates the output_file_pattern from a job name and format.
 * Lowercase, replace non-alphanumeric with underscores, trim edges.
 *
 * Example: "My Render", "JPEG" -> "//render/my_render_####.jpg"
 */
export function generateOutputFilePattern(name: string, format = 'PNG'): string {
  const ext = FORMAT_EXTENSIONS[format] || '.png';
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
  return `//render/${slug}_####${ext}`;
}

/**
 * Build the render_settings object for a single job or animation.
 * Tiled jobs do NOT include resolution in render_settings.
 * Conditionally includes format-specific keys (JPEG quality, EXR color depth).
 */
export function buildRenderSettings(
  samples: number,
  resolutionX: number,
  resolutionY: number,
  outputFormat = 'PNG',
  jpegQuality = 90,
  colorDepth = '16',
): Record<string, unknown> {
  const settings: Record<string, unknown> = {
    'cycles.samples': samples,
    'render.resolution_x': resolutionX,
    'render.resolution_y': resolutionY,
    'render.image_settings.file_format': outputFormat,
  };
  if (outputFormat === 'JPEG') {
    settings['render.image_settings.quality'] = jpegQuality;
  }
  if (outputFormat === 'OPEN_EXR' || outputFormat === 'OPEN_EXR_MULTILAYER') {
    settings['render.image_settings.color_depth'] = colorDepth;
  }
  return settings;
}

/**
 * Build render_settings for tiled jobs (no resolution keys).
 * EXR formats are blocked for tiled jobs, so color_depth is never included.
 */
export function buildTiledRenderSettings(
  samples: number,
  outputFormat = 'PNG',
  jpegQuality = 90,
): Record<string, unknown> {
  const settings: Record<string, unknown> = {
    'cycles.samples': samples,
    'render.image_settings.file_format': outputFormat,
  };
  if (outputFormat === 'JPEG') {
    settings['render.image_settings.quality'] = jpegQuality;
  }
  return settings;
}

/**
 * Parse a tiling config string like "4x4" into tile counts.
 */
export function parseTilingConfig(config: string): { tile_count_x: number; tile_count_y: number } {
  const match = config.match(/(\d+)x(\d+)/);
  if (!match) return { tile_count_x: 2, tile_count_y: 2 };
  return {
    tile_count_x: parseInt(match[1], 10),
    tile_count_y: parseInt(match[2], 10),
  };
}

/** Render engine options for the form dropdown. */
export const RENDER_ENGINES = [
  { value: 'CYCLES', label: 'Cycles' },
  { value: 'BLENDER_EEVEE_NEXT', label: 'Eevee' },
  { value: 'WORKBENCH', label: 'Workbench' },
];

/** Render device options for the form dropdown. */
export const RENDER_DEVICES = [
  { value: 'CPU', label: 'CPU Only' },
  { value: 'GPU', label: 'GPU Only' },
  { value: 'ANY', label: 'Any Available' },
];

/** Tiling options for tiled jobs (excludes NONE). */
export const TILING_OPTIONS = [
  { value: '2x2', label: '2x2 (4 Tiles)' },
  { value: '3x3', label: '3x3 (9 Tiles)' },
  { value: '4x4', label: '4x4 (16 Tiles)' },
  { value: '5x5', label: '5x5 (25 Tiles)' },
];

/** Tiling options for animations (includes NONE). */
export const ANIMATION_TILING_OPTIONS = [
  { value: 'NONE', label: 'None' },
  ...TILING_OPTIONS,
];

/** Output format options. */
export const OUTPUT_FORMATS = [
  { value: 'PNG', label: 'PNG' },
  { value: 'JPEG', label: 'JPEG' },
  { value: 'OPEN_EXR', label: 'OpenEXR' },
  { value: 'OPEN_EXR_MULTILAYER', label: 'OpenEXR MultiLayer' },
  { value: 'TIFF', label: 'TIFF' },
  { value: 'BMP', label: 'BMP' },
  { value: 'HDR', label: 'HDR (Radiance)' },
  { value: 'TARGA', label: 'Targa' },
];

/** Pillow-compatible output formats for tiled rendering. */
export const TILED_OUTPUT_FORMATS = OUTPUT_FORMATS.filter(
  f => !['OPEN_EXR', 'OPEN_EXR_MULTILAYER', 'HDR'].includes(f.value)
);

/**
 * Reverse of buildRenderSettings(). Extracts display-friendly values
 * from the render_settings JSON returned by the API.
 */
export function parseRenderSettings(
  renderSettings: Record<string, unknown>,
): { samples?: number; resolutionX?: number; resolutionY?: number } {
  return {
    samples: typeof renderSettings['cycles.samples'] === 'number'
      ? renderSettings['cycles.samples'] : undefined,
    resolutionX: typeof renderSettings['render.resolution_x'] === 'number'
      ? renderSettings['render.resolution_x'] : undefined,
    resolutionY: typeof renderSettings['render.resolution_y'] === 'number'
      ? renderSettings['render.resolution_y'] : undefined,
  };
}
