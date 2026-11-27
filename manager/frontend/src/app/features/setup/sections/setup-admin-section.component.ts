// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, Input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { SetupFieldErrorsComponent } from '../setup-field-errors.component';
import { errorsFor } from '../setup-errors';
import { FieldCodes, FieldErrors, PasswordConfirm, SetupForm } from '../setup.types';

/** The server's code on `admin.username` when the account exists (`validate_admin.py`). */
const ACCOUNT_EXISTS = 'account_exists';

/** Admin (FR-WZ-3, FR-WZ-11): keep the database's admins, or the first admin. */
@Component({
  selector: 'app-setup-admin-section',
  standalone: true,
  imports: [FormsModule, MatCheckboxModule, MatFormFieldModule, MatInputModule,
    SetupFieldErrorsComponent],
  template: `
    <section>
      <h2>Admin</h2>
      <app-setup-field-errors [messages]="err('admin')" />
      @if (form.admin.accounts_exist) {
        <mat-checkbox class="setup-block" [ngModel]="form.admin.keep_existing"
                      (ngModelChange)="setKeepExisting($event)">
          Keep the existing admin accounts
        </mat-checkbox>
      }
      <app-setup-field-errors [messages]="err('admin.keep_existing')" />
      @if (!form.admin.keep_existing) {
        <mat-form-field class="full-width">
          <mat-label>Username</mat-label>
          <input matInput [(ngModel)]="form.admin.username" autocomplete="username" />
        </mat-form-field>
        <app-setup-field-errors [messages]="err('admin.username')" />
        @if (offerReplace) {
          <mat-checkbox class="setup-block" [(ngModel)]="form.admin.replace_password">
            This account exists. Replace its password?
          </mat-checkbox>
        }
        <app-setup-field-errors [messages]="err('admin.replace_password')" />
        <mat-form-field class="full-width">
          <mat-label>Email (optional)</mat-label>
          <input matInput type="email" [(ngModel)]="form.admin.email" autocomplete="email" />
        </mat-form-field>
        <app-setup-field-errors [messages]="err('admin.email')" />
        <mat-form-field class="full-width">
          <mat-label>Password</mat-label>
          <input matInput type="password" [(ngModel)]="form.admin.password"
                 autocomplete="new-password" />
        </mat-form-field>
        <app-setup-field-errors [messages]="err('admin.password')" />
        <mat-form-field class="full-width">
          <mat-label>Password again</mat-label>
          <input matInput type="password" [(ngModel)]="confirm.admin" autocomplete="new-password" />
        </mat-form-field>
        @if (confirm.admin && confirm.admin !== form.admin.password) {
          <div class="setup-error" role="alert">The passwords are not the same.</div>
        }
      }
    </section>
  `,
})
export class SetupAdminSectionComponent {
  @Input({ required: true }) form!: SetupForm;
  @Input() errors: FieldErrors = {};
  /** The last refusal's codes by dotted key. */
  @Input() codes: FieldCodes = {};
  /** The "again" fields; the form checks them before it submits. */
  @Input({ required: true }) confirm!: PasswordConfirm;

  err(key: string): string[] {
    return errorsFor(this.errors, key);
  }

  /** The replace tick appears once the server said the account exists, or when it is set. */
  get offerReplace(): boolean {
    return this.form.admin.replace_password
      || (this.codes['admin.username'] ?? []).includes(ACCOUNT_EXISTS);
  }

  /** Keeping the existing admins replaces no password. */
  setKeepExisting(keep: boolean): void {
    this.form.admin.keep_existing = keep;
    if (keep) {
      this.form.admin.replace_password = false;
    }
  }
}
