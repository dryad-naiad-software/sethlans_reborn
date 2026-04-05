// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  Observable, interval, switchMap, startWith, shareReplay,
  fromEvent, map, distinctUntilChanged, EMPTY,
} from 'rxjs';
import { environment } from '../../../environments/environment';

/**
 * Creates a visibility-aware polling observable that repeatedly calls
 * the provided fetch function. Pauses polling when the browser tab
 * is hidden and resumes when it becomes visible again.
 *
 * @param fetchFn - Function that returns an Observable of the data to fetch.
 * @param intervalMs - Polling interval in milliseconds (defaults to environment config).
 * @returns An Observable that emits the latest result on each poll cycle.
 */
export function poll<T>(
  fetchFn: () => Observable<T>,
  intervalMs: number = environment.pollingIntervalMs,
): Observable<T> {
  const visible$ = fromEvent(document, 'visibilitychange').pipe(
    startWith(null),
    map(() => document.visibilityState === 'visible'),
    distinctUntilChanged(),
  );

  return visible$.pipe(
    switchMap(visible =>
      visible
        ? interval(intervalMs).pipe(startWith(0), switchMap(() => fetchFn()))
        : EMPTY
    ),
    shareReplay({ bufferSize: 1, refCount: true }),
  );
}
