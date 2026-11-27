// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  EMPTY, Observable, catchError, concat, defer, map, of, repeat, switchMap, takeWhile, timeout,
  timer,
} from 'rxjs';
import { SetupHealth } from './setup.types';

export const POLL_MS = 1000;
export const LIMIT_MS = 180_000;
const REQUEST_MS = 3000;

export type RestartState =
  | { kind: 'waiting' }
  | { kind: 'done' }
  | { kind: 'setup_again'; reason: string }
  | { kind: 'timeout' };

/**
 * One health answer (or `null`: refused, 502, no answer) as a state
 * (FR-WZ-8). The old setup process answers `setup` with its own `boot_id`
 * until it exits; that is still "waiting". With no old `boot_id` (it could
 * not be read before the submit), a setup answer cannot be told apart from
 * the old process, so only the running manager ends the wait.
 */
export function restartState(health: SetupHealth | null, oldBootId: string): RestartState {
  if (health === null) {
    return { kind: 'waiting' };
  }
  if (!oldBootId) {
    return health.mode === 'normal' ? { kind: 'done' } : { kind: 'waiting' };
  }
  if (health.boot_id === oldBootId) {
    return { kind: 'waiting' };
  }
  if (health.mode === 'normal') {
    return { kind: 'done' };
  }
  const reason = health.last_failure
    || 'Setup mode started again without a reason; check the launcher\'s log.';
  return { kind: 'setup_again', reason };
}

/**
 * Asks `health()` one second after each answer (or failed or hung
 * request), for at most `limitMs`; emits `waiting`
 * until the manager answers (`done`) or setup mode is back
 * (`setup_again`), then completes. Ends with `timeout` when neither came
 * in time.
 */
export function watchRestart(
  health: () => Observable<SetupHealth>,
  oldBootId: string,
  pollMs = POLL_MS,
  limitMs = LIMIT_MS,
): Observable<RestartState> {
  return defer(() => {
    const started = Date.now();
    let last: RestartState = { kind: 'waiting' };
    const polls = timer(pollMs).pipe(
      switchMap(() => health().pipe(
        timeout(REQUEST_MS),
        catchError(() => of(null)),
      )),
      repeat(),
      map(answer => (last = restartState(answer, oldBootId))),
      takeWhile(state => state.kind === 'waiting' && Date.now() - started < limitMs, true),
    );
    const ending = defer(() => (last.kind === 'waiting' ? of<RestartState>({ kind: 'timeout' }) : EMPTY));
    return concat(polls, ending);
  });
}
