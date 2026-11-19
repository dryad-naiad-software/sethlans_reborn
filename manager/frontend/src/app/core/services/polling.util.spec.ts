// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { HttpErrorResponse } from '@angular/common/http';
import { fakeAsync, tick } from '@angular/core/testing';
import { Observable, Subscription, of, throwError } from 'rxjs';
import { poll } from './polling.util';

const INTERVAL = 1000;

/** A fetch function that answers from a script: a non-number entry fails that call. */
function scripted(
  script: (number | Error | HttpErrorResponse)[],
): { fn: () => Observable<number>; calls: () => number } {
  let calls = 0;
  const fn = (): Observable<number> => {
    const step = script[Math.min(calls, script.length - 1)];
    calls++;
    return typeof step === 'number' ? of(step) : throwError(() => step);
  };
  return { fn, calls: () => calls };
}

describe('poll (#261)', () => {
  let sub: Subscription | undefined;
  let values: number[];
  let ended: string[];

  beforeEach(() => {
    spyOnProperty(document, 'visibilityState', 'get').and.returnValue('visible');
    values = [];
    ended = [];
  });

  afterEach(() => sub?.unsubscribe());

  function start(source: Observable<number>): void {
    sub = source.subscribe({
      next: v => values.push(v),
      error: () => ended.push('error'),
      complete: () => ended.push('complete'),
    });
  }

  it('keeps emitting after a fetch errors once and then succeeds', fakeAsync(() => {
    const fetch = scripted([new Error('blip'), 1, 2]);
    const onError = jasmine.createSpy('onError');
    start(poll(fetch.fn, onError, INTERVAL));

    expect(values).toEqual([]);
    expect(onError).toHaveBeenCalledOnceWith(jasmine.any(Error));
    tick(INTERVAL);
    tick(INTERVAL);
    expect(values).toEqual([1, 2]);
    expect(ended).toEqual([]);
    sub!.unsubscribe();
  }));

  it('never ends the stream on errors and reports one error per run of failures', fakeAsync(() => {
    const boom = new Error('down');
    const fetch = scripted([boom, boom, boom, 7, boom, 8]);
    const onError = jasmine.createSpy('onError');
    start(poll(fetch.fn, onError, INTERVAL));

    tick(INTERVAL * 2);
    expect(fetch.calls()).toBe(3);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith(boom);
    expect(ended).toEqual([]);

    tick(INTERVAL);
    expect(values).toEqual([7]);
    tick(INTERVAL);
    expect(onError).toHaveBeenCalledTimes(2);
    tick(INTERVAL);
    expect(values).toEqual([7, 8]);
    expect(ended).toEqual([]);
    sub!.unsubscribe();
  }));

  it('skips a fetch function that throws synchronously', fakeAsync(() => {
    let calls = 0;
    const fn = (): Observable<number> => {
      if (calls++ === 0) throw new Error('sync');
      return of(calls);
    };
    const onError = jasmine.createSpy('onError');
    start(poll(fn, onError, INTERVAL));
    tick(INTERVAL);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(values).toEqual([2]);
    expect(ended).toEqual([]);
    sub!.unsubscribe();
  }));

  it('skips failures silently without an error handler', fakeAsync(() => {
    const fetch = scripted([new Error('blip'), 5]);
    start(poll(fetch.fn, undefined, INTERVAL));
    tick(INTERVAL);
    expect(values).toEqual([5]);
    expect(ended).toEqual([]);
    sub!.unsubscribe();
  }));

  for (const status of [401, 403]) {
    it(`ends polling with the error on HTTP ${status}, without calling onError`, fakeAsync(() => {
      const denied = new HttpErrorResponse({ status });
      const fetch = scripted([1, denied, 2]);
      const onError = jasmine.createSpy('onError');
      let received: unknown;
      sub = poll(fetch.fn, onError, INTERVAL).subscribe({
        next: v => values.push(v),
        error: e => { received = e; ended.push('error'); },
      });
      tick(INTERVAL * 5);
      expect(values).toEqual([1]);
      expect(ended).toEqual(['error']);
      expect(received).toBe(denied);
      expect(onError).not.toHaveBeenCalled();
      expect(fetch.calls()).toBe(2);
    }));
  }

  for (const [label, status] of [['a 500', 500], ['a network error', 0]] as const) {
    it(`skips ${label} and keeps polling`, fakeAsync(() => {
      const failure = new HttpErrorResponse({ status });
      const fetch = scripted([failure, 3]);
      const onError = jasmine.createSpy('onError');
      start(poll(fetch.fn, onError, INTERVAL));
      tick(INTERVAL);
      expect(onError).toHaveBeenCalledOnceWith(failure);
      expect(values).toEqual([3]);
      expect(ended).toEqual([]);
      sub!.unsubscribe();
    }));
  }

  it('stops fetching once unsubscribed', fakeAsync(() => {
    const fetch = scripted([new Error('blip'), 1]);
    start(poll(fetch.fn, undefined, INTERVAL));
    tick(INTERVAL);
    expect(fetch.calls()).toBe(2);
    sub!.unsubscribe();
    tick(INTERVAL * 5);
    expect(fetch.calls()).toBe(2);
  }));
});
