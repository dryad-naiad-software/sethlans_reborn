// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, OnInit, inject, signal } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { SetupErrorCode } from '../../../../core/models/error-envelope';

const BOOTSTRAP_ERROR_STORAGE_KEY = 'sethlans.bootstrapError';

/** Navigation seam, overridable in tests. */
export const _bootstrapErrorNav = {
  goTo: (url: string): void => {
    window.location.href = url;
  },
};

type BootstrapErrorCode = SetupErrorCode | 'network_error';

interface StoredError {
  code: BootstrapErrorCode;
  message?: string;
}

@Component({
  selector: 'app-bootstrap-error',
  standalone: true,
  imports: [MatCardModule, MatButtonModule, MatIconModule],
  template: `
    <div class="container">
      <mat-card>
        <mat-card-header>
          <mat-icon class="error-icon" color="warn">error</mat-icon>
          <mat-card-title>Setup bootstrap failed</mat-card-title>
          <mat-card-subtitle>Code: {{ code() }}</mat-card-subtitle>
        </mat-card-header>
        <mat-card-content>
          <p class="message">{{ message() }}</p>
          <p class="hint">
            Re-copy the Setup URL from the launcher console and open it in a
            fresh tab.
          </p>
        </mat-card-content>
        <mat-card-actions>
          <button mat-raised-button color="primary" (click)="retry()">
            <mat-icon>refresh</mat-icon> Retry
          </button>
        </mat-card-actions>
      </mat-card>
    </div>
  `,
  styles: [`
    .container {
      display: flex;
      justify-content: center;
      padding: 48px 16px;
    }
    mat-card {
      max-width: 560px;
      width: 100%;
    }
    .error-icon {
      margin-right: 8px;
    }
    .message {
      margin: 16px 0;
      font-size: 15px;
    }
    .hint {
      color: rgba(0, 0, 0, 0.6);
      font-size: 13px;
    }
  `],
})
export class BootstrapErrorComponent implements OnInit {
  private readonly route = inject(ActivatedRoute);

  code = signal<BootstrapErrorCode>('internal_error');
  message = signal<string>('Bootstrap failed.');

  ngOnInit(): void {
    const stored = this.readAndClearStored();
    const queryCode = this.route.snapshot.queryParamMap.get('code') as
      | BootstrapErrorCode
      | null;
    const effectiveCode = stored?.code ?? queryCode ?? 'internal_error';
    this.code.set(effectiveCode);
    this.message.set(stored?.message ?? this.copyFor(effectiveCode));
  }

  retry(): void {
    // Full reload of root; user re-opens Setup URL from launcher console.
    _bootstrapErrorNav.goTo('/setup/');
  }

  private readAndClearStored(): StoredError | null {
    try {
      if (typeof sessionStorage === 'undefined') {
        return null;
      }
      const raw = sessionStorage.getItem(BOOTSTRAP_ERROR_STORAGE_KEY);
      if (!raw) {
        return null;
      }
      sessionStorage.removeItem(BOOTSTRAP_ERROR_STORAGE_KEY);
      const parsed = JSON.parse(raw) as StoredError;
      return parsed;
    } catch {
      return null;
    }
  }

  private copyFor(code: BootstrapErrorCode): string {
    switch (code) {
      case 'invalid_token':
        return 'Token expired or incorrect — copy the Setup URL from the launcher console again.';
      case 'rate_limited':
        return 'Too many attempts. Wait a few minutes and try again.';
      default:
        return 'Bootstrap failed. Check the launcher console for the setup URL.';
    }
  }
}
