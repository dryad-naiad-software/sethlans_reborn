// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, output } from '@angular/core';
import { ReactiveFormsModule, FormBuilder, Validators } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';
import { NetworkRequest } from '../models/setup.models';

@Component({
  selector: 'app-setup-network',
  standalone: true,
  imports: [
    ReactiveFormsModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
    MatExpansionModule,
    MatProgressSpinnerModule,
    MatSlideToggleModule,
    MatSnackBarModule,
  ],
  template: `
    <h2>Network Configuration</h2>
    <p class="description">Configure how the manager listens for connections.</p>

    <form [formGroup]="form" (ngSubmit)="onSubmit()">
      <div class="toggle-row">
        <mat-slide-toggle formControlName="allow_remote">
          Allow workers on the network to connect
        </mat-slide-toggle>
        <p class="toggle-hint">
          @if (form.get('allow_remote')?.value) {
            Listens on all network interfaces (0.0.0.0)
          } @else {
            Listens on localhost only (127.0.0.1)
          }
        </p>
      </div>

      <mat-expansion-panel class="advanced-panel">
        <mat-expansion-panel-header>
          <mat-panel-title>Advanced</mat-panel-title>
        </mat-expansion-panel-header>
        <mat-form-field class="full-width">
          <mat-label>Bind Host</mat-label>
          <input matInput formControlName="bind_host" />
          <mat-hint>Override the bind address</mat-hint>
        </mat-form-field>
        <mat-form-field class="full-width">
          <mat-label>Bind Port</mat-label>
          <input matInput type="number" formControlName="bind_port" />
        </mat-form-field>
        <mat-form-field class="full-width">
          <mat-label>Data Directory</mat-label>
          <input matInput formControlName="data_dir" />
          <mat-hint>Leave blank for default location</mat-hint>
        </mat-form-field>
      </mat-expansion-panel>

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
    .full-width { width: 100%; margin-bottom: 16px; }
    .toggle-row { margin-bottom: 24px; }
    .toggle-hint { font-size: 13px; color: rgba(0, 0, 0, 0.5); margin-top: 8px; }
    .advanced-panel { margin-bottom: 24px; }
    .actions { margin-top: 16px; }
  `],
})
export class NetworkComponent {
  private readonly fb = inject(FormBuilder);
  private readonly api = inject(SetupApiService);
  private readonly state = inject(SetupStateService);
  private readonly snackBar = inject(MatSnackBar);

  readonly stepComplete = output<void>();
  submitting = false;

  form = this.fb.group({
    allow_remote: [true],
    bind_host: ['0.0.0.0'],
    bind_port: [8080, [Validators.required, Validators.min(1), Validators.max(65535)]],
    data_dir: [''],
  });

  onSubmit(): void {
    if (this.form.invalid || this.submitting) return;
    this.submitting = true;

    const val = this.form.getRawValue();
    const bindHost = val.bind_host || (val.allow_remote ? '0.0.0.0' : '127.0.0.1');
    const req: NetworkRequest = {
      bind_host: bindHost,
      bind_port: val.bind_port!,
    };
    if (val.data_dir) {
      req.data_dir = val.data_dir;
    }

    this.api.configureNetwork(req).subscribe({
      next: () => {
        this.state.markCheckpoint('network_configured');
        this.submitting = false;
        this.stepComplete.emit();
      },
      error: (err) => {
        this.submitting = false;
        const msg = err.error?.detail || 'Failed to configure network';
        this.snackBar.open(msg, 'Dismiss', { duration: 5000 });
      },
    });
  }
}
