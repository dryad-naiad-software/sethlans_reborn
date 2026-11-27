// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { formNotices, otherErrors, sectionHasErrors, sectionsFor } from './setup-errors';
import { SetupMode, SetupNotice } from './setup.types';

describe('setup-errors', () => {
  /** FR-WZ-3: Admin and Advanced for manager modes, Worker with a worker, Manager for worker only. */
  const sections: [SetupMode, string[]][] = [
    ['manager', ['mode', 'admin', 'advanced', 'data_folder']],
    ['manager_worker', ['mode', 'admin', 'advanced', 'worker', 'data_folder']],
    ['worker', ['mode', 'worker', 'manager', 'data_folder']],
  ];
  for (const [mode, expected] of sections) {
    it(`shows the FR-WZ-3 sections for ${mode}`, () => {
      expect(sectionsFor(mode)).toEqual(expected);
    });
  }

  /** FR-WZ-2: a dotted key belongs to its section; bare section keys count too. */
  it('finds a section by any of its dotted keys', () => {
    expect(sectionHasErrors({ 'database.host': ['Bad host.'] }, 'advanced')).toBeTrue();
    expect(sectionHasErrors({ manager: ['Pick one.'] }, 'manager')).toBeTrue();
    expect(sectionHasErrors({ 'database.host': [] }, 'advanced')).toBeFalse();
    expect(sectionHasErrors({ 'database.host': ['Bad host.'] }, 'admin')).toBeFalse();
  });

  /**
   * FR-WZ-2: non_field_errors and unknown keys go to the top list; keys of
   * a section (shown or hidden by the mode) never do, so a stale message of
   * a hidden section is not shown.
   */
  it('lists only non-field and unknown keys as other errors', () => {
    expect(otherErrors({
      non_field_errors: ['The worker program failed.'],
      'worker.gpu_split': ['Unknown option.'],
      'admin.username': ['Required.'],
      'manager.url': ['Use https.'],
    })).toEqual(['The worker program failed.', 'worker.gpu_split: Unknown option.']);
  });

  /** FR-WZ-1: the form's notices hide those about the part the mode does not install. */
  const notices: [SetupMode, string[]][] = [
    ['manager', ['Database line', 'Settings line']],
    ['manager_worker', ['Worker line', 'Database line', 'Settings line']],
    ['worker', ['Worker line', 'Settings line']],
  ];
  for (const [mode, expected] of notices) {
    it(`keeps the form notices that apply to ${mode}`, () => {
      const lines: SetupNotice[] = [
        { part: 'worker', message: 'Worker line' },
        { part: 'database', message: 'Database line' },
        { part: 'settings', message: 'Settings line' },
      ];
      expect(formNotices(lines, mode)).toEqual(expected);
    });
  }
});
