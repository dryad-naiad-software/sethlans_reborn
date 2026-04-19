// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  AfterViewInit,
  Component,
  DestroyRef,
  ElementRef,
  OnDestroy,
  ViewChild,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import {
  FormControl,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';
import { startWith } from 'rxjs/operators';
import { HttpErrorResponse } from '@angular/common/http';
import { Router } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { SetupBootstrapService } from '../../services/setup-bootstrap.service';
import { SetupErrorEnvelope } from '../../../../core/models/error-envelope';
import {
  formatCountdown,
  secondsRemaining,
} from '../../utils/countdown.util';

const RATE_LIMIT_SECONDS = 300;

@Component({
  selector: 'app-token-entry',
  standalone: true,
  imports: [
    ReactiveFormsModule,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    MatProgressSpinnerModule,
  ],
  templateUrl: './token-entry.component.html',
  styleUrls: ['./token-entry.component.scss'],
})
export class TokenEntryComponent implements AfterViewInit, OnDestroy {
  private readonly bootstrapService = inject(SetupBootstrapService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  @ViewChild('tokenInput') private tokenInput?: ElementRef<HTMLInputElement>;

  /** Randomized name attribute to suppress password-manager capture. */
  readonly fieldName = `token-${crypto.randomUUID().slice(0, 8)}`;

  readonly token = new FormControl('', {
    nonNullable: true,
    validators: [Validators.required],
  });

  readonly submitting = signal(false);
  readonly errorMessage = signal<string | null>(null);
  readonly showToken = signal(false);
  readonly showRetry = signal(false);
  /** True while a successful 204 left us stranded (navigation failure). */
  readonly navigationStuck = signal(false);

  /** Epoch-ms deadline for the rate-limit countdown; null when not limited. */
  readonly rateLimitedUntil = signal<number | null>(null);
  /** Drives the visible countdown. Ticks every 1s via setInterval. */
  readonly countdownSeconds = signal(0);

  readonly rateLimited = computed(() => this.rateLimitedUntil() !== null);

  readonly countdownDisplay = computed(() =>
    formatCountdown(this.countdownSeconds()),
  );

  private readonly tokenValue = toSignal(
    this.token.valueChanges.pipe(startWith('')),
    { initialValue: '' },
  );

  readonly submitDisabled = computed(
    () =>
      this.submitting() ||
      this.rateLimited() ||
      this.navigationStuck() ||
      this.tokenValue().trim().length === 0,
  );

  private countdownHandle: ReturnType<typeof setInterval> | null = null;

  ngAfterViewInit(): void {
    // Auto-focus the token field on mount.
    queueMicrotask(() => this.tokenInput?.nativeElement.focus());
  }

  ngOnDestroy(): void {
    this.clearCountdown();
  }

  toggleVisibility(): void {
    this.showToken.update((v) => !v);
  }

  onRetry(): void {
    this.showRetry.set(false);
    this.errorMessage.set(null);
    this.onSubmit();
  }

  onSubmit(): void {
    if (this.submitDisabled()) {
      return;
    }
    const value = this.token.value.trim();
    if (!value) {
      return;
    }
    this.submitting.set(true);
    this.errorMessage.set(null);
    this.showRetry.set(false);

    this.bootstrapService
      .bootstrap(value)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => this.onBootstrapSuccess(),
        error: (err: HttpErrorResponse) => this.handleError(err),
      });
  }

  private onBootstrapSuccess(): void {
    this.router.navigate(['/setup/wizard']).then(
      (ok) => {
        this.submitting.set(false);
        if (!ok) {
          this.setNavigationStuck();
        }
      },
      () => {
        this.submitting.set(false);
        this.setNavigationStuck();
      },
    );
  }

  private setNavigationStuck(): void {
    this.navigationStuck.set(true);
    this.errorMessage.set('Setup started. Reload to continue.');
  }

  private handleError(err: HttpErrorResponse): void {
    this.submitting.set(false);

    if (err.status === 0) {
      this.errorMessage.set(
        'Cannot reach the manager. Is the launcher still running?',
      );
      this.showRetry.set(true);
      return;
    }

    const envelope = err.error as SetupErrorEnvelope | undefined;
    const code = envelope?.error?.code;

    if (err.status === 429 || code === 'rate_limited') {
      this.startRateLimitCountdown();
      this.errorMessage.set(
        'Too many attempts. Try again in ~5 minutes.',
      );
      return;
    }

    if (err.status === 403 && code === 'invalid_token') {
      this.errorMessage.set('Invalid token. Check and retry.');
      queueMicrotask(() => {
        const el = this.tokenInput?.nativeElement;
        if (el) {
          el.focus();
          el.select();
        }
      });
      return;
    }

    // 404 setup_complete is handled by the global auth interceptor; pass through.
    // Anything else: generic service-unavailable copy.
    this.errorMessage.set('Setup service unavailable. Reload to retry.');
  }

  private startRateLimitCountdown(): void {
    const deadline = Date.now() + RATE_LIMIT_SECONDS * 1000;
    this.rateLimitedUntil.set(deadline);
    this.countdownSeconds.set(RATE_LIMIT_SECONDS);
    this.clearCountdown();
    this.countdownHandle = setInterval(() => {
      const remaining = secondsRemaining(deadline);
      this.countdownSeconds.set(remaining);
      if (remaining <= 0) {
        this.clearCountdown();
        this.rateLimitedUntil.set(null);
        this.errorMessage.set(null);
      }
    }, 1000);
  }

  private clearCountdown(): void {
    if (this.countdownHandle !== null) {
      clearInterval(this.countdownHandle);
      this.countdownHandle = null;
    }
  }
}
