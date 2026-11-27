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
import { FieldErrors, PasswordConfirm, SetupForm } from '../setup.types';
import { SetupCardsComponent } from './setup-cards.component';

/** Worker (FR-WZ-3, FR-WZ-4): cards, Unreal, and the worker page password (OQ-4). */
@Component({
  selector: 'app-setup-worker-section',
  standalone: true,
  imports: [FormsModule, MatCheckboxModule, MatFormFieldModule, MatInputModule,
    SetupFieldErrorsComponent, SetupCardsComponent],
  template: `
    @let worker = form.worker;
    <section>
      <h2>Worker</h2>
      <app-setup-field-errors [messages]="err('worker')" />
      <app-setup-cards [form]="form" [errors]="errors" />

      <mat-checkbox class="setup-block" [(ngModel)]="worker.unreal_enabled"
                    [disabled]="worker.unreal_enabled_env">
        Render Unreal Engine jobs
      </mat-checkbox>
      @if (worker.unreal_enabled_env) {
        <p class="setup-note">An environment variable sets this; it is not changed here.</p>
      }
      <app-setup-field-errors [messages]="err('worker.unreal_enabled')" />

      <p class="setup-note">
        The worker's own web page has its own password, separate from the admin password.
      </p>
      <mat-form-field class="full-width">
        <mat-label>Worker page password</mat-label>
        <input matInput type="password" [(ngModel)]="worker.ui_password"
               autocomplete="new-password"
               [placeholder]="worker.ui_password_set ? 'Empty: keep the current password' : ''" />
      </mat-form-field>
      <app-setup-field-errors [messages]="err('worker.ui_password')" />
      <mat-form-field class="full-width">
        <mat-label>Worker page password again</mat-label>
        <input matInput type="password" [(ngModel)]="confirm.worker" autocomplete="new-password" />
      </mat-form-field>
      @if (confirm.worker !== worker.ui_password && (confirm.worker || worker.ui_password)) {
        <div class="setup-error" role="alert">The passwords are not the same.</div>
      }
    </section>
  `,
})
export class SetupWorkerSectionComponent {
  @Input({ required: true }) form!: SetupForm;
  @Input() errors: FieldErrors = {};
  @Input({ required: true }) confirm!: PasswordConfirm;

  err(key: string): string[] {
    return errorsFor(this.errors, key);
  }
}
