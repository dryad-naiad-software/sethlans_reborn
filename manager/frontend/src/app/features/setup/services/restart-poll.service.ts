// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { Observable, Subject, timer, of } from 'rxjs';
import { catchError, map, switchMap, takeUntil, takeWhile } from 'rxjs/operators';
import { SetupApiService } from './setup-api.service';

export type PollOutcome = 'boot_changed' | 'timed_out';

// Exponential backoff delays in ms, linear at cap after (FR-15 step 5).
// 1s, 2s, 4s, 8s, then 8s repeat up to 120s budget.
const POLL_DELAYS_MS = [1000, 2000, 4000, 8000, 8000, 8000, 8000, 8000,
  8000, 8000, 8000, 8000, 8000, 8000, 8000, 8000, 8000];
const TOTAL_BUDGET_MS = 120_000;

/**
 * Polls /api/health/ until the reported boot_id differs from bootIdBefore
 * or the 120s budget expires. Emits 'boot_changed' on success,
 * 'timed_out' on failure. Network errors (status 0) are treated as
 * "keep polling" until the budget expires.
 */
@Injectable({ providedIn: 'root' })
export class RestartPollService {
  private readonly api = inject(SetupApiService);

  poll(bootIdBefore: string): Observable<PollOutcome> {
    const cancel$ = new Subject<void>();
    const startedAt = Date.now();

    return new Observable<PollOutcome>((subscriber) => {
      let attempt = 0;

      const scheduleNext = (): void => {
        const elapsed = Date.now() - startedAt;
        if (elapsed >= TOTAL_BUDGET_MS) {
          subscriber.next('timed_out');
          subscriber.complete();
          return;
        }
        const delay = POLL_DELAYS_MS[Math.min(attempt, POLL_DELAYS_MS.length - 1)];
        attempt++;
        timer(delay)
          .pipe(
            takeUntil(cancel$),
            switchMap(() =>
              this.api.getHealth().pipe(
                map((h) => ({ ok: true, bootId: h.boot_id })),
                catchError((err: HttpErrorResponse) => {
                  // status 0 (network unreachable) → keep polling.
                  if (err.status === 0) {
                    return of({ ok: false as const, bootId: null });
                  }
                  // Non-zero status: manager is up. Treat as ok; attempt to
                  // read a boot_id if envelope carried one; else fall back
                  // to "changed" so we proceed to login.
                  return of({ ok: true as const, bootId: null });
                }),
              ),
            ),
          )
          .subscribe((result) => {
            if (result.ok && result.bootId && result.bootId !== bootIdBefore) {
              subscriber.next('boot_changed');
              subscriber.complete();
              return;
            }
            if (result.ok && !result.bootId) {
              // Non-health 2xx or non-zero error — manager is up, assume boot
              // changed.
              subscriber.next('boot_changed');
              subscriber.complete();
              return;
            }
            scheduleNext();
          });
      };

      scheduleNext();

      return () => {
        cancel$.next();
        cancel$.complete();
      };
    }).pipe(takeWhile((_, i) => i === 0, true));
  }
}
