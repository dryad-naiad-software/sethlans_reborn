// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, Input, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { SetupFieldErrorsComponent } from '../setup-field-errors.component';
import { errorsFor } from '../setup-errors';
import { FieldErrors, SetupForm } from '../setup.types';

/** Data folder, every mode (FR-WZ-3, FR-DF). */
@Component({
  selector: 'app-setup-data-folder-section',
  standalone: true,
  imports: [FormsModule, MatFormFieldModule, MatInputModule, SetupFieldErrorsComponent],
  template: `
    @let folder = form.data_folder;
    <section>
      <h2>Data folder</h2>
      <app-setup-field-errors [messages]="err('data_folder')" />
      <mat-form-field class="full-width">
        <mat-label>Data folder</mat-label>
        <input matInput [(ngModel)]="folder.path" spellcheck="false" />
        <mat-hint>An absolute path; it is created if it does not exist.</mat-hint>
      </mat-form-field>
      <app-setup-field-errors [messages]="err('data_folder.path')" />
      @if (folder.free_bytes !== null && folder.path === checkedPath) {
        <p class="setup-note">{{ gigabytes(folder.free_bytes) }} GB free on its drive.</p>
      }
      @if (folder.low_space && folder.path === checkedPath) {
        <p class="setup-warning">Renders and tools need 20 GB or more.</p>
      }
    </section>
  `,
})
export class SetupDataFolderSectionComponent implements OnInit {
  @Input({ required: true }) form!: SetupForm;
  @Input() errors: FieldErrors = {};

  /** The free space was measured for this path only; the submit checks a new one. */
  checkedPath = '';

  ngOnInit(): void {
    this.checkedPath = this.form.data_folder.path;
  }

  err(key: string): string[] {
    return errorsFor(this.errors, key);
  }

  gigabytes(bytes: number): number {
    return Math.floor(bytes / 1000 ** 3);
  }
}
