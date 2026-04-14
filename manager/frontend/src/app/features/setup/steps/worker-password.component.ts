// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, output, OnInit } from '@angular/core';
import { ReactiveFormsModule, FormBuilder, Validators } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';

@Component({
  selector: 'app-setup-worker-password',
  standalone: true,
  imports: [
    ReactiveFormsModule,
    MatFormFieldModule,
    MatInputModule,
    MatCheckboxModule,
    MatButtonModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
  ],
  template: `
    <h2>Worker Password</h2>
    <p class="description">
      Set the password for the built-in worker on this machine.
    </p>

    <form [formGroup]="form" (ngSubmit)="onSubmit()">
      <mat-checkbox
        [checked]="useSameAsAdmin"
        (change)="onToggleSamePassword($event.checked)">
        Use same password as admin account
      </mat-checkbox>

      <mat-form-field class="full-width password-field">
        <mat-label>Worker Password</mat-label>
        <input matInput type="password" formControlName="password" />
        @if (form.get('password')?.hasError('minlength')) {
          <mat-error>Password must be at least 8 characters</mat-error>
        }
      </mat-form-field>

      <div class="actions">
        <button mat-raised-button color="primary" type="submit"
                [disabled]="form.invalid || submitting">
          @if (submitting) {
            <mat-spinner diameter="20" />
          } @else {
            Continue
          }
        </button>
      </div>
    </form>
  `,
  styles: [`
    .description { color: rgba(0, 0, 0, 0.6); margin-bottom: 24px; }
    .full-width { width: 100%; }
    .password-field { margin-top: 16px; }
    .actions { margin-top: 16px; }
  `],
})
export class WorkerPasswordComponent implements OnInit {
  private readonly fb = inject(FormBuilder);
  private readonly api = inject(SetupApiService);
  private readonly state = inject(SetupStateService);
  private readonly snackBar = inject(MatSnackBar);

  readonly stepComplete = output<void>();
  submitting = false;
  useSameAsAdmin = true;

  form = this.fb.group({
    password: ['', [Validators.required, Validators.minLength(8)]],
  });

  ngOnInit(): void {
    this.applyAdminPassword();
  }

  onToggleSamePassword(checked: boolean): void {
    this.useSameAsAdmin = checked;
    if (checked) {
      this.applyAdminPassword();
    } else {
      this.form.patchValue({ password: '' });
    }
  }

  private applyAdminPassword(): void {
    const adminPwd = this.state.getAdminPassword();
    if (adminPwd && this.useSameAsAdmin) {
      this.form.patchValue({ password: adminPwd });
    }
  }

  onSubmit(): void {
    if (this.form.invalid || this.submitting) return;
    this.submitting = true;

    this.api.setWorkerPassword({ password: this.form.value.password! }).subscribe({
      next: () => {
        this.state.markCheckpoint('worker_password_set');
        this.submitting = false;
        this.stepComplete.emit();
      },
      error: (err) => {
        this.submitting = false;
        const msg = err.error?.detail || 'Failed to set worker password';
        this.snackBar.open(msg, 'Dismiss', { duration: 5000 });
      },
    });
  }
}
