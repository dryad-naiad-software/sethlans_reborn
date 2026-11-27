// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { HttpErrorResponse, HttpInterceptorFn, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { SetupApiService, answersOf, toFailure } from './setup-api.service';
import { setupForm } from './setup.fixtures';
import { SetupFailure } from './setup.types';

/** Stands in for the running app's interceptors (CSRF, 401/403 handling). */
const appInterceptor: HttpInterceptorFn = (req, next) =>
  next(req.clone({ setHeaders: { 'X-App-Interceptor': 'yes' } }));

async function failureOf(status: number, error: unknown): Promise<SetupFailure> {
  try {
    await firstValueFrom(toFailure(new HttpErrorResponse({ status, error })));
  } catch (failure) {
    return failure as SetupFailure;
  }
  throw new Error('toFailure did not fail');
}

describe('SetupApiService', () => {
  let service: SetupApiService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(withInterceptors([appInterceptor])), provideHttpClientTesting()],
    });
    service = TestBed.inject(SetupApiService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  /** FR-SM-3, FR-WZ-2: the session from auth goes in X-Setup-Session on form and submit. */
  it('sends the session token from auth as X-Setup-Session', async () => {
    const authed = firstValueFrom(service.auth('ABCD-1234'));
    const authReq = http.expectOne('/api/setup/auth/');
    expect(authReq.request.method).toBe('POST');
    expect(authReq.request.body).toEqual({ token: 'ABCD-1234' });
    authReq.flush({ session_token: 's3ss10n' });
    await authed;
    expect(service.hasSession).toBeTrue();

    service.form().subscribe();
    const formReq = http.expectOne('/api/setup/form/');
    expect(formReq.request.headers.get('X-Setup-Session')).toBe('s3ss10n');
    formReq.flush(setupForm());

    const answers = setupForm();
    service.submit(answers).subscribe();
    const submitReq = http.expectOne('/api/setup/submit/');
    expect(submitReq.request.method).toBe('POST');
    // S1: only the answer parts are sent.
    expect(Object.keys(submitReq.request.body).sort()).toEqual(
      ['admin', 'data_folder', 'database', 'manager', 'mode', 'server', 'worker']);
    expect(submitReq.request.body.data_folder).toEqual({ path: answers.data_folder.path });
    expect(submitReq.request.headers.get('X-Setup-Session')).toBe('s3ss10n');
    submitReq.flush({});

    service.clearSession();
    expect(service.hasSession).toBeFalse();
  });

  /** FR-SM-3: setup mode has no login; the app's interceptors must not touch its calls. */
  it('uses an HttpClient without the app interceptors', () => {
    service.health().subscribe();
    const req = http.expectOne('/api/health/');
    expect(req.request.headers.has('X-App-Interceptor')).toBeFalse();
    req.flush({ mode: 'setup', boot_id: 'b1' });
  });

  /** FR-WZ-2: an error answer of any call arrives as a SetupFailure. */
  it('turns an error answer into a SetupFailure', async () => {
    const result = firstValueFrom(service.submit(setupForm()));
    http.expectOne('/api/setup/submit/').flush(
      { error: 'conflict', message: 'Setup is already being saved, or was saved.' },
      { status: 409, statusText: 'Conflict' });
    await expectAsync(result).toBeRejectedWith(jasmine.objectContaining({
      status: 409, code: 'conflict', message: 'Setup is already being saved, or was saved.',
    }));
  });
});

/** FR-WZ-2: the error bodies of `views.py`, `submit.py` and `worker_outcome.py`, made uniform. */
describe('toFailure', () => {
  const cases: { why: string; status: number; body: unknown; expected: SetupFailure }[] = [
    {
      why: '400 invalid: fields and codes by dotted key, non-text and non-list values dropped',
      status: 400,
      body: { error: 'invalid', message: 'Some answers need fixing.',
        fields: { 'admin.username': ['Required.', 7], 'server.port': 'x' },
        codes: { 'admin.username': ['account_exists', 3], 'server.port': 'x' } },
      expected: { status: 400, code: 'invalid', message: 'Some answers need fixing.',
        fields: { 'admin.username': ['Required.'] }, codes: { 'admin.username': ['account_exists'] } },
    },
    {
      why: '400 worker refusal: code, message and the field it points at',
      status: 400,
      body: { error: 'invalid_key', message: 'The key is wrong.',
        fields: { 'manager.enrollment_key': ['The key is wrong.'] } },
      expected: { status: 400, code: 'invalid_key', message: 'The key is wrong.',
        fields: { 'manager.enrollment_key': ['The key is wrong.'] }, codes: {} },
    },
    {
      why: '403 not_authenticated',
      status: 403,
      body: { error: 'not_authenticated', message: 'Enter the setup token first.' },
      expected: { status: 403, code: 'not_authenticated', message: 'Enter the setup token first.',
        fields: {}, codes: {} },
    },
    {
      why: '500 without a JSON body: the status is the message',
      status: 500,
      body: '<html>Internal Server Error</html>',
      expected: { status: 500, code: '', message: 'The setup server answered 500.', fields: {},
        codes: {} },
    },
    {
      why: 'no answer at all (status 0)',
      status: 0,
      body: null,
      expected: { status: 0, code: '', message: 'The setup server could not be reached.',
        fields: {}, codes: {} },
    },
  ];

  for (const c of cases) {
    it(`normalises ${c.why}`, async () => {
      expect(await failureOf(c.status, c.body)).toEqual(c.expected);
    });
  }
});

/** Review S1, N2: the submit sends copies of the answer parts only, the typed manager URL trimmed. */
describe('answersOf', () => {
  it('copies the answer parts and trims the manager URL', () => {
    const form = setupForm();
    form.manager.url = '  https://studio.local:8080 \n';
    form.worker.cards = ['gpu0'];

    const answers = answersOf(form);

    expect(answers).toEqual({
      mode: form.mode, server: form.server, data_folder: { path: form.data_folder.path },
      database: form.database, admin: form.admin, worker: form.worker,
      manager: { ...form.manager, url: 'https://studio.local:8080' },
    });
    expect(answers.worker.cards).not.toBe(form.worker.cards);
    expect(form.manager.url).toBe('  https://studio.local:8080 \n');
  });
});
