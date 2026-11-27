// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { SetupApiService } from './setup-api.service';
import { SetupFailure } from './setup.types';

/** Step 1: the setup token the launcher printed (FR-SEC-1). */
@Component({
  selector: 'app-setup-token',
  standalone: true,
  imports: [FormsModule, MatButtonModule, MatFormFieldModule, MatInputModule,
    MatProgressSpinnerModule],
  template: `
    <section>
      <h2>Setup token</h2>
      <p class="setup-note">
        Enter the setup token shown by the Sethlans launcher (its window or the
        tray's Copy Setup Token).
      </p>
      @if (notice) {
        <p class="setup-warning" role="alert">{{ notice }}</p>
      }
      <form (ngSubmit)="send()">
        <mat-form-field class="full-width">
          <mat-label>Setup token</mat-label>
          <input matInput name="token" [(ngModel)]="token" autocomplete="off"
                 spellcheck="false" required />
        </mat-form-field>
        @if (error) {
          <div class="setup-error" role="alert">{{ error }}</div>
        }
        <button mat-raised-button color="primary" type="submit"
                [disabled]="!token.trim() || sending">
          @if (sending) {
            <mat-spinner diameter="20" />
          } @else {
            Continue
          }
        </button>
      </form>
    </section>
  `,
})
export class SetupTokenComponent {
  private readonly api = inject(SetupApiService);

  /** A message from the page, e.g. "the session expired". */
  @Input() notice = '';
  @Output() authenticated = new EventEmitter<void>();

  token = '';
  error = '';
  sending = false;

  send(): void {
    const token = this.token.trim();
    if (!token || this.sending) {
      return;
    }
    this.sending = true;
    this.error = '';
    this.api.auth(token).subscribe({
      next: () => {
        this.sending = false;
        this.token = '';
        this.authenticated.emit();
      },
      error: (failure: SetupFailure) => {
        this.sending = false;
        this.error = failure.message;
      },
    });
  }
}
