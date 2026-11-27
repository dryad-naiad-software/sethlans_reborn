// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { EnrollmentKeyNoticeService } from './enrollment-key-notice.service';

const URL = '/api/auth/enrollment_key_notice/';

describe('EnrollmentKeyNoticeService', () => {
  let service: EnrollmentKeyNoticeService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    service = TestBed.inject(EnrollmentKeyNoticeService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  /** FR-WZ-9: the notice is read with GET and dismissed with an empty POST on the same path. */
  it('reads with GET and dismisses with a bodyless POST', () => {
    service.get().subscribe();
    const read = http.expectOne(URL);
    expect(read.request.method).toBe('GET');
    read.flush({ show: true, enrollment_key: 'ABCD-EFGH-JKMN-PQRS' });

    service.dismiss().subscribe();
    const dismiss = http.expectOne(URL);
    expect(dismiss.request.method).toBe('POST');
    expect(dismiss.request.body).toBeNull();
    dismiss.flush({ show: false, enrollment_key: null });
  });
});
