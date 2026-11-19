// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  Observable, interval, switchMap, startWith, shareReplay,
  fromEvent, map, distinctUntilChanged, EMPTY, defer, catchError, tap,
} from 'rxjs';
import { HttpErrorResponse } from '@angular/common/http';
import { environment } from '../../../environments/environment';

/** Called when a poll's fetch fails; see {@link poll}. */
export type PollErrorHandler = (error: unknown) => void;

/**
 * 401 and 403 answers end polling: retrying cannot help, and the auth
 * interceptor already redirects (401) or shows a snackbar (403) per answer.
 */
function endsPolling(error: unknown): boolean {
  return error instanceof HttpErrorResponse && (error.status === 401 || error.status === 403);
}

/**
 * Creates a visibility-aware polling observable that repeatedly calls
 * the provided fetch function. Pauses polling when the browser tab
 * is hidden and resumes when it becomes visible again.
 *
 * A failed fetch is skipped and the next tick fetches again (#261), except an
 * `HttpErrorResponse` with status 401 or 403: that one ends the stream with
 * the error, so the subscriber's `error` callback runs once, and `onError`
 * is not called for it.
 *
 * @param fetchFn - Function that returns an Observable of the data to fetch.
 * @param onError - Optional handler called with a skipped error when fetches
 *   start failing: once for the first failure after the start or after a
 *   success, not again for each further failure in a row.
 * @param intervalMs - Polling interval in milliseconds (defaults to environment config).
 * @returns An Observable that emits the latest result of each successful poll.
 */
export function poll<T>(
  fetchFn: () => Observable<T>,
  onError?: PollErrorHandler,
  intervalMs: number = environment.pollingIntervalMs,
): Observable<T> {
  return defer(() => {
    let failing = false;
    const fetchOnce = (): Observable<T> => defer(fetchFn).pipe(
      tap(() => { failing = false; }),
      catchError((error: unknown) => {
        if (endsPolling(error)) throw error;
        if (!failing) {
          failing = true;
          onError?.(error);
        }
        return EMPTY;
      }),
    );

    const visible$ = fromEvent(document, 'visibilitychange').pipe(
      startWith(null),
      map(() => document.visibilityState === 'visible'),
      distinctUntilChanged(),
    );

    return visible$.pipe(
      switchMap(visible =>
        visible
          ? interval(intervalMs).pipe(startWith(0), switchMap(fetchOnce))
          : EMPTY
      ),
    );
  }).pipe(shareReplay({ bufferSize: 1, refCount: true }));
}
