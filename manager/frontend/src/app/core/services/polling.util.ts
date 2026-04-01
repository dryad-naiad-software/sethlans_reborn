// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Observable, interval, switchMap, startWith, shareReplay } from 'rxjs';
import { environment } from '../../../environments/environment';

/**
 * Creates a polling observable that repeatedly calls the provided fetch function.
 * Uses switchMap to cancel in-flight requests when a new interval tick arrives.
 *
 * @param fetchFn - Function that returns an Observable of the data to fetch.
 * @param intervalMs - Polling interval in milliseconds (defaults to environment config).
 * @returns An Observable that emits the latest result on each poll cycle.
 */
export function poll<T>(
  fetchFn: () => Observable<T>,
  intervalMs: number = environment.pollingIntervalMs,
): Observable<T> {
  return interval(intervalMs).pipe(
    startWith(0),
    switchMap(() => fetchFn()),
    shareReplay({ bufferSize: 1, refCount: true }),
  );
}
