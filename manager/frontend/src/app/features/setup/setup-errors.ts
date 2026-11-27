// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { FieldErrors, SetupMode, SetupNotice } from './setup.types';

export const NON_FIELD = 'non_field_errors';

/**
 * The dotted keys a section shows next to its own fields (or at its top).
 * Any other key the server sends is shown in the list at the top of the
 * form, so no message is ever lost.
 */
export const SECTION_KEYS: Record<string, readonly string[]> = {
  mode: ['mode'],
  admin: ['admin', 'admin.keep_existing', 'admin.username', 'admin.email',
    'admin.password', 'admin.replace_password'],
  advanced: ['server', 'server.host', 'server.port', 'database', 'database.engine',
    'database.name', 'database.host', 'database.port', 'database.user',
    'database.password', 'database.tls', 'database.tls_mode', 'database.tls_ca_file'],
  worker: ['worker', 'worker.compute', 'worker.cards', 'worker.combine',
    'worker.unreal_enabled', 'worker.ui_password'],
  manager: ['manager', 'manager.url', 'manager.name', 'manager.enrollment_key',
    'manager.keep_enrollment'],
  data_folder: ['data_folder', 'data_folder.path'],
};

/** The sections shown in each mode (FR-WZ-3). */
export function sectionsFor(mode: SetupMode): string[] {
  const sections = ['mode'];
  if (mode !== 'worker') {
    sections.push('admin', 'advanced');
  }
  if (mode !== 'manager') {
    sections.push('worker');
  }
  if (mode === 'worker') {
    sections.push('manager');
  }
  sections.push('data_folder');
  return sections;
}

export function errorsFor(errors: FieldErrors, key: string): string[] {
  return errors[key] ?? [];
}

/** True when any key of the section has a message. */
export function sectionHasErrors(errors: FieldErrors, section: string): boolean {
  return (SECTION_KEYS[section] ?? []).some(key => errorsFor(errors, key).length > 0);
}

/**
 * Messages no section shows: `non_field_errors` and unknown keys. Keys of
 * a section the mode hides are left out: they are from an answer sent in
 * another mode, which the server does not check in this mode.
 */
export function otherErrors(errors: FieldErrors): string[] {
  const known = new Set(Object.values(SECTION_KEYS).flat());
  return Object.entries(errors)
    .filter(([key]) => !known.has(key))
    .flatMap(([key, messages]) =>
      key === NON_FIELD ? messages : messages.map(message => `${key}: ${message}`));
}

/**
 * The form's notices that apply to the mode: a manager-only install has no
 * worker program and a worker-only one has no database, so the lines about
 * that part are hidden (`form.py`, `worker_form.py`).
 */
export function formNotices(notices: SetupNotice[], mode: SetupMode): string[] {
  const hidden: SetupNotice['part'] | null = mode === 'manager' ? 'worker'
    : mode === 'worker' ? 'database' : null;
  return notices.filter(notice => notice.part !== hidden).map(notice => notice.message);
}
