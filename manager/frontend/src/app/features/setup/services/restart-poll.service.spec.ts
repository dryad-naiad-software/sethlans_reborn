// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed, fakeAsync, tick, flush } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { of, throwError, Subject } from 'rxjs';
import { RestartPollService, PollOutcome } from './restart-poll.service';
import { SetupApiService } from './setup-api.service';

type Health = { boot_id: string; setup_mode: boolean };

describe('RestartPollService', () => {
  let service: RestartPollService;
  let mockApi: jasmine.SpyObj<SetupApiService>;

  beforeEach(() => {
    mockApi = jasmine.createSpyObj('SetupApiService', ['getHealth']);
    TestBed.configureTestingModule({
      providers: [{ provide: SetupApiService, useValue: mockApi }],
    });
    service = TestBed.inject(RestartPollService);
  });

  it('is created', () => {
    expect(service).toBeTruthy();
  });

  it('emits boot_changed when returned boot_id differs from initial',
    fakeAsync(() => {
      mockApi.getHealth.and.returnValue(
        of<Health>({ boot_id: 'new-uuid', setup_mode: false }),
      );

      const outcomes: PollOutcome[] = [];
      service.poll('old-uuid').subscribe(o => outcomes.push(o));

      // First backoff tick = 1000ms
      tick(1000);
      expect(mockApi.getHealth).toHaveBeenCalledTimes(1);
      expect(outcomes[0]).toBe('boot_changed');
    }),
  );

  it('keeps polling when boot_id unchanged', fakeAsync(() => {
    mockApi.getHealth.and.returnValue(
      of<Health>({ boot_id: 'same', setup_mode: true }),
    );
    const outcomes: PollOutcome[] = [];
    service.poll('same').subscribe(o => outcomes.push(o));

    tick(1000);
    expect(mockApi.getHealth).toHaveBeenCalledTimes(1);
    expect(outcomes.length).toBe(0);

    tick(2000);
    expect(mockApi.getHealth).toHaveBeenCalledTimes(2);
    expect(outcomes.length).toBe(0);

    tick(4000);
    expect(mockApi.getHealth).toHaveBeenCalledTimes(3);

    // Flush remaining to avoid pending-timer error from fakeAsync.
    flush();
  }));

  it('continues polling on status 0 network errors', fakeAsync(() => {
    const err = new HttpErrorResponse({ status: 0, statusText: 'Unknown' });
    let callCount = 0;
    mockApi.getHealth.and.callFake(() => {
      callCount++;
      if (callCount < 3) {
        return throwError(() => err);
      }
      return of<Health>({ boot_id: 'new', setup_mode: false });
    });

    const outcomes: PollOutcome[] = [];
    service.poll('old').subscribe(o => outcomes.push(o));

    // 1s
    tick(1000);
    expect(callCount).toBe(1);
    // +2s
    tick(2000);
    expect(callCount).toBe(2);
    // +4s
    tick(4000);
    expect(callCount).toBe(3);
    expect(outcomes[0]).toBe('boot_changed');
  }));

  it('emits timed_out after 120s budget', fakeAsync(() => {
    // Keep returning same boot_id forever.
    mockApi.getHealth.and.returnValue(
      of<Health>({ boot_id: 'same', setup_mode: true }),
    );

    const outcomes: PollOutcome[] = [];
    service.poll('same').subscribe(o => outcomes.push(o));

    // Exponential 1+2+4+8=15s, then 8s linear. Run 120+ seconds.
    tick(130_000);
    expect(outcomes[0]).toBe('timed_out');
  }));

  it('aborts cleanly via unsubscribe before first tick', fakeAsync(() => {
    mockApi.getHealth.and.returnValue(
      of<Health>({ boot_id: 'new', setup_mode: false }),
    );

    const outcomes: PollOutcome[] = [];
    const sub = service.poll('old').subscribe(o => outcomes.push(o));
    sub.unsubscribe();

    tick(5000);
    expect(mockApi.getHealth).not.toHaveBeenCalled();
    expect(outcomes.length).toBe(0);
  }));

  it('does not poll before the first 1s backoff elapses', fakeAsync(() => {
    mockApi.getHealth.and.returnValue(
      of<Health>({ boot_id: 'same', setup_mode: true }),
    );
    service.poll('same').subscribe();

    tick(999);
    expect(mockApi.getHealth).not.toHaveBeenCalled();
    tick(1);
    expect(mockApi.getHealth).toHaveBeenCalledTimes(1);

    flush();
  }));
});
