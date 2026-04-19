// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { provideHttpClient, HttpErrorResponse } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { SetupBootstrapService } from './setup-bootstrap.service';

describe('SetupBootstrapService', () => {
  let service: SetupBootstrapService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    sessionStorage.clear();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(SetupBootstrapService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('is created', () => {
    expect(service).toBeTruthy();
  });

  it('POSTs {token} to /api/setup/bootstrap/ with withCredentials', () => {
    service.bootstrap('setup-token-value').subscribe();
    const req = httpMock.expectOne('/api/setup/bootstrap/');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ token: 'setup-token-value' });
    expect(req.request.withCredentials).toBeTrue();
    req.flush(null, { status: 204, statusText: 'No Content' });
  });

  it('completes the observable on 204 No Content', (done) => {
    let nextEmitted = false;
    service.bootstrap('abc').subscribe({
      next: () => { nextEmitted = true; },
      complete: () => {
        expect(nextEmitted).toBeTrue();
        done();
      },
    });
    const req = httpMock.expectOne('/api/setup/bootstrap/');
    req.flush(null, { status: 204, statusText: 'No Content' });
  });

  it('propagates HttpErrorResponse on 403 invalid_token', (done) => {
    service.bootstrap('bad').subscribe({
      next: () => fail('expected error'),
      error: (err: HttpErrorResponse) => {
        expect(err.status).toBe(403);
        expect(err.error?.error?.code).toBe('invalid_token');
        done();
      },
    });
    const req = httpMock.expectOne('/api/setup/bootstrap/');
    req.flush(
      { error: { code: 'invalid_token', message: 'bad', details: {} } },
      { status: 403, statusText: 'Forbidden' },
    );
  });

  it('propagates HttpErrorResponse on 429 rate_limited', (done) => {
    service.bootstrap('token').subscribe({
      next: () => fail('expected error'),
      error: (err: HttpErrorResponse) => {
        expect(err.status).toBe(429);
        done();
      },
    });
    const req = httpMock.expectOne('/api/setup/bootstrap/');
    req.flush(
      { error: { code: 'rate_limited', message: 'slow', details: {} } },
      { status: 429, statusText: 'Too Many Requests' },
    );
  });

  it('propagates HttpErrorResponse on network error (status 0)', (done) => {
    service.bootstrap('token').subscribe({
      next: () => fail('expected error'),
      error: (err: HttpErrorResponse) => {
        expect(err.status).toBe(0);
        done();
      },
    });
    const req = httpMock.expectOne('/api/setup/bootstrap/');
    req.error(new ProgressEvent('error'), { status: 0, statusText: 'Unknown' });
  });
});
