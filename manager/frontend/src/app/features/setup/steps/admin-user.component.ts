// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, output } from '@angular/core';
import {
  ReactiveFormsModule,
  FormBuilder,
  Validators,
  AbstractControl,
  ValidationErrors,
} from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';

function passwordMatchValidator(control: AbstractControl): ValidationErrors | null {
  const password = control.get('password');
  const confirm = control.get('password_confirm');
  if (password && confirm && password.value !== confirm.value) {
    return { passwordMismatch: true };
  }
  return null;
}

@Component({
  selector: 'app-setup-admin-user',
  standalone: true,
  imports: [
    ReactiveFormsModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
  ],
  template: `
    <h2>Create Admin Account</h2>
    <p class="description">Create the administrator account for the manager.</p>

    <form [formGroup]="form" (ngSubmit)="onSubmit()">
      <mat-form-field class="full-width">
        <mat-label>Username</mat-label>
        <input matInput formControlName="username" />
      </mat-form-field>

      <mat-form-field class="full-width">
        <mat-label>Email</mat-label>
        <input matInput type="email" formControlName="email" />
      </mat-form-field>

      <mat-form-field class="full-width">
        <mat-label>Password</mat-label>
        <input matInput type="password" formControlName="password" />
      </mat-form-field>

      <mat-form-field class="full-width">
        <mat-label>Confirm Password</mat-label>
        <input matInput type="password" formControlName="password_confirm" />
        @if (form.hasError('passwordMismatch')) {
          <mat-error>Passwords do not match</mat-error>
        }
      </mat-form-field>

      @if (errorMessage) {
        <p class="error-text">{{ errorMessage }}</p>
      }

      <div class="actions">
        <button mat-raised-button color="primary" type="submit"
                [disabled]="form.invalid || submitting">
          @if (submitting) {
            <mat-spinner diameter="20" />
          } @else {
            Create Account
          }
        </button>
        @if (adminExists) {
          <button mat-button type="button" (click)="continueWithExisting()">
            Continue with existing account
          </button>
        }
      </div>
    </form>
  `,
  styles: [`
    .description { color: rgba(0, 0, 0, 0.6); margin-bottom: 24px; }
    .full-width { width: 100%; margin-bottom: 16px; }
    .actions { margin-top: 16px; }
    .error-text { color: #f44336; font-size: 14px; margin-bottom: 16px; }
  `],
})
export class AdminUserComponent {
  private readonly fb = inject(FormBuilder);
  private readonly api = inject(SetupApiService);
  private readonly state = inject(SetupStateService);
  private readonly snackBar = inject(MatSnackBar);

  readonly stepComplete = output<void>();
  submitting = false;
  errorMessage = '';
  adminExists = false;

  form = this.fb.group({
    username: ['', Validators.required],
    email: ['', [Validators.required, Validators.email]],
    password: ['', [Validators.required, Validators.minLength(8)]],
    password_confirm: ['', Validators.required],
  }, { validators: passwordMatchValidator });

  onSubmit(): void {
    if (this.form.invalid || this.submitting) return;
    this.submitting = true;
    this.errorMessage = '';

    const val = this.form.getRawValue();
    this.api.createAdminUser({
      username: val.username!,
      email: val.email!,
      password: val.password!,
      password_confirm: val.password_confirm!,
    }).subscribe({
      next: () => {
        this.state.setAdminPassword(val.password!);
        this.state.markCheckpoint('admin_created');
        this.submitting = false;
        this.stepComplete.emit();
      },
      error: (err: HttpErrorResponse) => {
        this.submitting = false;
        if (err.status === 409) {
          this.adminExists = true;
          this.errorMessage = 'An admin account already exists.';
        } else {
          this.errorMessage = err.error?.detail || 'Failed to create admin account';
        }
      },
    });
  }

  continueWithExisting(): void {
    this.state.markCheckpoint('admin_created');
    this.stepComplete.emit();
  }
}
