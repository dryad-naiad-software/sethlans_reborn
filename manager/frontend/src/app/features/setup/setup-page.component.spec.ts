// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { SetupApiService } from './setup-api.service';
import { SetupPageComponent } from './setup-page.component';
import { finishInfo, setupForm } from './setup.fixtures';
import { SetupFailure, SetupHealth } from './setup.types';

// Health's fixed last_failure summaries (#348), shown exactly as they are.
const FIRST_START = "The manager's first start failed. Enter the setup token to see why.";
const DID_NOT_START = 'The manager did not start. Enter the setup token to see why.';
const EXIT_CODE = 'The manager stopped with exit code 3';

function failure(status: number, message: string): SetupFailure {
  return { status, code: status === 403 ? 'not_authenticated' : 'x', message, fields: {}, codes: {} };
}

describe('SetupPageComponent', () => {
  let page: SetupPageComponent;
  let api: jasmine.SpyObj<SetupApiService>;

  beforeEach(() => {
    api = jasmine.createSpyObj('SetupApiService', ['health', 'form', 'clearSession']);
    TestBed.configureTestingModule({
      imports: [SetupPageComponent],
      providers: [{ provide: SetupApiService, useValue: api }],
    });
    // The steps are driven through the class; the child steps have their own specs.
    page = TestBed.createComponent(SetupPageComponent).componentInstance;
  });

  /** FR-WZ-10, FR-WZ-8: the first health answer picks the first step. */
  const starts: { why: string; health: SetupHealth | null; step: string; notice: string }[] = [
    { why: 'the manager is running', health: { mode: 'normal', boot_id: 'b' }, step: 'normal', notice: '' },
    { why: 'setup mode', health: { mode: 'setup', boot_id: 'b' }, step: 'token', notice: '' },
    { why: '#348 setup mode after a failed start: the summary as it is, no prefix',
      health: { mode: 'setup', boot_id: 'b', last_failure: FIRST_START }, step: 'token', notice: FIRST_START },
    { why: '#348 setup mode after an exit code: the summary as it is, no prefix',
      health: { mode: 'setup', boot_id: 'b', last_failure: EXIT_CODE }, step: 'token', notice: EXIT_CODE },
    { why: 'setup mode with last_failure null', health: { mode: 'setup', boot_id: 'b', last_failure: null },
      step: 'token', notice: '' },
    { why: 'no health answer', health: null, step: 'token', notice: '' },
  ];
  for (const c of starts) {
    it(`starts on the right step: ${c.why}`, () => {
      api.health.and.returnValue(c.health ? of(c.health) : throwError(() => failure(0, 'x')));
      page.ngOnInit();
      expect(page.step).toBe(c.step as SetupPageComponent['step']);
      expect(page.notice).toBe(c.notice);
    });
  }

  /** FR-WZ-1, FR-WZ-8: the form arrives with the current boot_id for the finish page. */
  it('shows the form with the boot_id read alongside it', () => {
    const form = setupForm();
    api.form.and.returnValue(of(form));
    api.health.and.returnValue(of({ mode: 'setup', boot_id: 'current' }));
    page.loadForm();
    expect(page.step).toBe('form');
    expect(page.form).toBe(form);
    expect(page.bootId).toBe('current');
  });

  /** FR-SM-3: a lost session (403) goes back to the token; anything else is shown. */
  const loads: { why: string; failure: SetupFailure; step: string; notice: string; cleared: number }[] = [
    { why: '403', failure: failure(403, 'Enter the setup token first.'), step: 'token',
      notice: 'The setup session ended; enter the setup token again.', cleared: 1 },
    { why: '500', failure: failure(500, 'The setup server answered 500.'), step: 'broken',
      notice: 'The form could not be read: The setup server answered 500.', cleared: 0 },
  ];
  for (const c of loads) {
    it(`handles a form read failure: ${c.why}`, () => {
      api.form.and.returnValue(throwError(() => c.failure));
      api.health.and.returnValue(of({ mode: 'setup', boot_id: 'b' }));
      page.loadForm();
      expect(page.step).toBe(c.step as SetupPageComponent['step']);
      expect(page.notice).toBe(c.notice);
      expect(api.clearSession).toHaveBeenCalledTimes(c.cleared);
    });
  }

  /** FR-WZ-8, #348: setup mode back with a new process: token step with the summary as it is. */
  it('#348 goes back to the token with the health summary, not prefixed, when setup mode is back', () => {
    page.finished(finishInfo());
    expect(page.step).toBe('finish');

    page.setupAgain(DID_NOT_START);

    expect(api.clearSession).toHaveBeenCalled();
    expect(page.step).toBe('token');
    expect(page.finish).toBeNull();
    expect(page.notice).toBe(DID_NOT_START);
  });
});
