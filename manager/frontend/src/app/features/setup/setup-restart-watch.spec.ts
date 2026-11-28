// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { fakeAsync, tick } from '@angular/core/testing';
import { NEVER, Observable, of, throwError } from 'rxjs';
import { LIMIT_MS, POLL_MS, RestartState, restartState, watchRestart } from './setup-restart-watch';
import { SetupHealth } from './setup.types';

const OLD = 'old-boot';
// One of health's fixed last_failure summaries (#348).
const DID_NOT_START = 'The manager did not start. Enter the setup token to see why.';

describe('setup restart watch', () => {
  /** FR-WZ-8: what one health answer means while the manager restarts. */
  const answers: { why: string; health: SetupHealth | null; expected: RestartState; old?: string }[] = [
    { why: 'refused, 502 or no answer', health: null, expected: { kind: 'waiting' } },
    { why: 'the old setup process, still up', health: { mode: 'setup', boot_id: OLD },
      expected: { kind: 'waiting' } },
    { why: 'the manager with a new boot_id', health: { mode: 'normal', boot_id: 'new' },
      expected: { kind: 'done' } },
    { why: '#348 setup mode back with its summary, passed on exactly',
      health: { mode: 'setup', boot_id: 'new', last_failure: DID_NOT_START },
      expected: { kind: 'setup_again', reason: DID_NOT_START } },
    { why: 'setup mode back without a reason', health: { mode: 'setup', boot_id: 'new', last_failure: null },
      expected: { kind: 'setup_again',
        reason: 'The manager did not start, and setup mode gave no reason; check the launcher\'s log.' } },
    // Review S3: with no old boot_id a setup answer may be the old process; only the manager ends it.
    { why: 'no old boot_id, a setup answer', old: '',
      health: { mode: 'setup', boot_id: 'any', last_failure: 'Port 8080 is in use.' },
      expected: { kind: 'waiting' } },
    { why: 'no old boot_id, the manager', old: '', health: { mode: 'normal', boot_id: 'any' },
      expected: { kind: 'done' } },
  ];
  for (const c of answers) {
    it(`reads a health answer: ${c.why}`, () => {
      expect(restartState(c.health, c.old ?? OLD)).toEqual(c.expected);
    });
  }

  /** FR-WZ-8: health every second, refused counts as restarting, a new manager ends the watch. */
  it('polls every second until the manager answers', fakeAsync(() => {
    const replies: Observable<SetupHealth>[] = [
      throwError(() => ({ status: 502 })),
      of({ mode: 'setup', boot_id: OLD }),
      of({ mode: 'normal', boot_id: 'new' }),
    ];
    const health = jasmine.createSpy('health').and.callFake(() => replies.shift()!);
    const states: string[] = [];
    let complete = false;
    watchRestart(health, OLD).subscribe({
      next: state => states.push(state.kind),
      complete: () => (complete = true),
    });

    tick(POLL_MS - 1);
    expect(health).not.toHaveBeenCalled();
    tick(1);
    expect(health).toHaveBeenCalledTimes(1);
    tick(2 * POLL_MS);

    expect(health).toHaveBeenCalledTimes(3);
    expect(states).toEqual(['waiting', 'waiting', 'done']);
    expect(complete).toBeTrue();
  }));

  /** FR-WZ-8: setup mode back ends the watch with its reason. */
  it('ends with setup_again when setup mode is back', fakeAsync(() => {
    const states: RestartState[] = [];
    watchRestart(() => of({ mode: 'setup', boot_id: 'new', last_failure: 'Bad database.' }), OLD)
      .subscribe(state => states.push(state));
    tick(POLL_MS * 3);
    expect(states).toEqual([{ kind: 'setup_again', reason: 'Bad database.' }]);
  }));

  /** Review N1: the next request goes 1 s after the last answer, also after a hung one times out. */
  it('waits 1 s after each answer, also after a hung request', fakeAsync(() => {
    const health = jasmine.createSpy('health').and.returnValue(NEVER);
    const subscription = watchRestart(health, OLD).subscribe();

    tick(POLL_MS);
    expect(health).toHaveBeenCalledTimes(1);
    tick(3000 + POLL_MS - 1);
    expect(health).toHaveBeenCalledTimes(1);
    tick(1);
    expect(health).toHaveBeenCalledTimes(2);
    subscription.unsubscribe();
  }));

  /** FR-WZ-8: up to 180 s; a hanging request counts as no answer. */
  it('times out after 180 s with no new answer', fakeAsync(() => {
    let last: RestartState | undefined;
    let complete = false;
    watchRestart(() => NEVER, OLD).subscribe({
      next: state => (last = state),
      complete: () => (complete = true),
    });

    tick(LIMIT_MS - POLL_MS);
    expect(last).toEqual({ kind: 'waiting' });
    expect(complete).toBeFalse();
    tick(2 * POLL_MS + 3000);
    expect(last).toEqual({ kind: 'timeout' });
    expect(complete).toBeTrue();
  }));
});
