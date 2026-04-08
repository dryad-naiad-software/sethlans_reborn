// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

export type ResolutionGroup = 'horizontal' | 'cinema' | 'vertical' | 'square';

export interface ResolutionPreset {
  id: string;
  label: string;
  x: number;
  y: number;
  group: ResolutionGroup;
}

export const RESOLUTION_PRESETS: ResolutionPreset[] = [
  // Horizontal (16:9)
  { id: 'hd720',        label: '720p',              x: 1280, y: 720,  group: 'horizontal' },
  { id: 'fhd1080',      label: '1080p',             x: 1920, y: 1080, group: 'horizontal' },
  { id: 'qhd1440',      label: '1440p (2K QHD)',    x: 2560, y: 1440, group: 'horizontal' },
  { id: 'uhd4k',        label: '4K UHD',            x: 3840, y: 2160, group: 'horizontal' },
  { id: 'uhd8k',        label: '8K UHD',            x: 7680, y: 4320, group: 'horizontal' },
  // Cinema (DCI)
  { id: 'dci2k',        label: 'DCI 2K',            x: 2048, y: 1080, group: 'cinema'     },
  { id: 'dci4k',        label: 'DCI 4K',            x: 4096, y: 2160, group: 'cinema'     },
  // Vertical (9:16)
  { id: 'hd720v',       label: '720p Vertical',     x: 720,  y: 1280, group: 'vertical'   },
  { id: 'fhd1080v',     label: '1080p Vertical',    x: 1080, y: 1920, group: 'vertical'   },
  { id: 'qhd1440v',     label: '1440p Vertical',    x: 1440, y: 2560, group: 'vertical'   },
  { id: 'uhd4kv',       label: '4K Vertical',       x: 2160, y: 3840, group: 'vertical'   },
  // Square (1:1)
  { id: 'sq1080',       label: '1080 Square',       x: 1080, y: 1080, group: 'square'     },
  { id: 'sq2160',       label: '2160 Square',       x: 2160, y: 2160, group: 'square'     },
];

export const DEFAULT_PRESET_ID = 'fhd1080';

export const PRESET_GROUP_LABELS: Record<ResolutionGroup, string> = {
  horizontal: 'Horizontal (16:9)',
  cinema:     'Cinema (DCI)',
  vertical:   'Vertical (9:16)',
  square:     'Square (1:1)',
};

export function findPresetByXY(x: number, y: number): ResolutionPreset | null {
  return RESOLUTION_PRESETS.find(p => p.x === x && p.y === y) ?? null;
}
