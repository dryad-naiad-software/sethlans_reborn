// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, Input } from '@angular/core';

/**
 * The server's messages for one field, shown under it. Not an alert: the
 * form's one summary line is, and takes the focus after a failed submit.
 */
@Component({
  selector: 'app-setup-field-errors',
  standalone: true,
  template: `
    @for (message of messages; track $index) {
      <div class="setup-error">{{ message }}</div>
    }
  `,
})
export class SetupFieldErrorsComponent {
  @Input() messages: string[] = [];
}
