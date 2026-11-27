// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { Clipboard } from '@angular/cdk/clipboard';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Subscription } from 'rxjs';
import { AuthService } from '../../core/services/auth.service';
import { EnrollmentKeyNoticeService } from '../../core/services/enrollment-key-notice.service';

/**
 * The one-time enrollment key notice on the dashboard (setup-rebuild
 * FR-WZ-9): shown to admins until one of them dismisses it. Non-admins
 * never ask (the endpoint is admin only), so they see nothing.
 */
@Component({
  selector: 'app-enrollment-key-notice',
  standalone: true,
  imports: [MatButtonModule, MatCardModule],
  template: `
    @if (key) {
      <mat-card class="notice">
        <mat-card-header>
          <mat-card-title>Worker enrollment key</mat-card-title>
        </mat-card-header>
        <mat-card-content>
          <p>Workers on other machines need this key to join this manager.
            Enter it in the worker's setup.</p>
          <code class="key">{{ key }}</code>
        </mat-card-content>
        <mat-card-actions>
          <button mat-button type="button" (click)="copy()">{{ copied ? 'Copied' : 'Copy' }}</button>
          <button mat-button type="button" (click)="dismiss()" [disabled]="dismissing">Dismiss</button>
        </mat-card-actions>
      </mat-card>
    }
  `,
  styles: [`
    .notice { margin-top: 16px; max-width: 600px; }
    .key {
      display: block; padding: 8px; font-family: monospace; font-size: 18px;
      background: rgba(0, 0, 0, 0.05); word-break: break-all; user-select: all;
    }
  `],
})
export class EnrollmentKeyNoticeComponent implements OnInit, OnDestroy {
  private readonly auth = inject(AuthService);
  private readonly notices = inject(EnrollmentKeyNoticeService);
  private readonly clipboard = inject(Clipboard);
  private readonly snackBar = inject(MatSnackBar);
  private subscription?: Subscription;

  key: string | null = null;
  copied = false;
  dismissing = false;

  ngOnInit(): void {
    if (!this.auth.user?.is_staff) {
      return;
    }
    this.subscription = this.notices.get().subscribe({
      next: notice => {
        this.key = notice.show ? notice.enrollment_key : null;
      },
      // The notice is optional: a failure leaves the dashboard as it is.
      error: () => {
        this.key = null;
      },
    });
  }

  ngOnDestroy(): void {
    this.subscription?.unsubscribe();
  }

  copy(): void {
    if (this.key && this.clipboard.copy(this.key)) {
      this.copied = true;
    } else {
      this.snackBar.open('Could not copy; select the key and copy it by hand.', 'Dismiss', { duration: 5000 });
    }
  }

  dismiss(): void {
    this.dismissing = true;
    this.subscription?.unsubscribe();
    this.subscription = this.notices.dismiss().subscribe({
      next: () => {
        this.key = null;
        this.dismissing = false;
      },
      error: () => {
        this.dismissing = false;
        this.snackBar.open('Could not dismiss the notice; try again.', 'Dismiss', { duration: 5000 });
      },
    });
  }
}
