// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, output, OnDestroy } from '@angular/core';
import { ReactiveFormsModule, FormBuilder, Validators } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatRadioModule } from '@angular/material/radio';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { Subscription, interval, switchMap, takeWhile, of, catchError, filter } from 'rxjs';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';
import { DatabaseRequest, SetupStatus } from '../models/setup.models';

type DbEngine = 'sqlite' | 'postgresql' | 'mysql' | 'custom';

@Component({
  selector: 'app-setup-database',
  standalone: true,
  imports: [
    ReactiveFormsModule,
    MatFormFieldModule,
    MatInputModule,
    MatRadioModule,
    MatButtonModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
  ],
  template: `
    <h2>Database Configuration</h2>
    <p class="description">Choose the database engine for the manager.</p>

    @if (waitingForRestart) {
      <div class="restart-message">
        <mat-spinner diameter="40" />
        <p>Manager is restarting with the new database configuration...</p>
      </div>
    } @else {
      <form [formGroup]="form" (ngSubmit)="onSubmit()">
        <mat-radio-group formControlName="engine" class="engine-group">
          <mat-radio-button value="sqlite">SQLite (default)</mat-radio-button>
          <mat-radio-button value="postgresql">PostgreSQL</mat-radio-button>
          <mat-radio-button value="mysql">MySQL</mat-radio-button>
          <mat-radio-button value="custom">Custom Engine</mat-radio-button>
        </mat-radio-group>

        @if (selectedEngine !== 'sqlite') {
          @if (selectedEngine === 'custom') {
            <mat-form-field class="full-width">
              <mat-label>Engine Path</mat-label>
              <input matInput formControlName="engine_path" />
              <mat-hint>Python module path (e.g. django.db.backends.oracle)</mat-hint>
            </mat-form-field>
          }

          <mat-form-field class="full-width">
            <mat-label>Host</mat-label>
            <input matInput formControlName="host" />
          </mat-form-field>

          <mat-form-field class="full-width">
            <mat-label>Port</mat-label>
            <input matInput formControlName="port" />
          </mat-form-field>

          <mat-form-field class="full-width">
            <mat-label>Database Name</mat-label>
            <input matInput formControlName="name" />
          </mat-form-field>

          <mat-form-field class="full-width">
            <mat-label>Username</mat-label>
            <input matInput formControlName="user" />
          </mat-form-field>

          <mat-form-field class="full-width">
            <mat-label>Password</mat-label>
            <input matInput type="password" formControlName="password" />
          </mat-form-field>
        }

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
    }
  `,
  styles: [`
    .description { color: rgba(0, 0, 0, 0.6); margin-bottom: 24px; }
    .engine-group {
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin-bottom: 24px;
    }
    .full-width { width: 100%; margin-bottom: 16px; }
    .actions { margin-top: 16px; }
    .restart-message {
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 48px;
      gap: 16px;
    }
    .restart-message p { color: rgba(0, 0, 0, 0.6); }
  `],
})
export class DatabaseComponent implements OnDestroy {
  private readonly fb = inject(FormBuilder);
  private readonly api = inject(SetupApiService);
  private readonly state = inject(SetupStateService);
  private readonly snackBar = inject(MatSnackBar);

  readonly stepComplete = output<void>();
  submitting = false;
  waitingForRestart = false;
  private pollSub: Subscription | null = null;

  form = this.fb.group({
    engine: ['sqlite' as DbEngine, Validators.required],
    host: [''],
    port: [''],
    name: [''],
    user: [''],
    password: [''],
    engine_path: [''],
  });

  get selectedEngine(): DbEngine {
    return this.form.get('engine')!.value as DbEngine;
  }

  onSubmit(): void {
    if (this.form.invalid || this.submitting) return;
    this.submitting = true;

    const val = this.form.getRawValue();
    const req: DatabaseRequest = { engine: val.engine as DbEngine };
    if (val.engine !== 'sqlite') {
      if (val.host) req.host = val.host;
      if (val.port) req.port = val.port;
      if (val.name) req.name = val.name;
      if (val.user) req.user = val.user;
      if (val.password) req.password = val.password;
    }
    if (val.engine === 'custom' && val.engine_path) {
      req.engine_path = val.engine_path;
    }

    this.api.configureDatabase(req).subscribe({
      next: (res) => {
        this.submitting = false;
        this.state.markCheckpoint('database_configured');
        if (res.status === 'restart_required') {
          this.waitingForRestart = true;
          this.pollForRestart();
        } else {
          this.stepComplete.emit();
        }
      },
      error: (err) => {
        this.submitting = false;
        const msg = err.error?.detail || 'Failed to configure database';
        this.snackBar.open(msg, 'Dismiss', { duration: 5000 });
      },
    });
  }

  private pollForRestart(): void {
    this.pollSub = interval(2000).pipe(
      switchMap(() => this.api.getStatus().pipe(
        catchError(() => of(null)),
      )),
      filter((status): status is SetupStatus => status !== null),
      takeWhile((status) => !status.checkpoints.includes('database_configured'), true),
    ).subscribe({
      next: (status) => {
        if (status.checkpoints.includes('database_configured')) {
          this.waitingForRestart = false;
          this.stepComplete.emit();
        }
      },
    });
  }

  ngOnDestroy(): void {
    this.pollSub?.unsubscribe();
  }
}
