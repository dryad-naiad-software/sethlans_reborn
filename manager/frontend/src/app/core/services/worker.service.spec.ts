// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { WorkerService } from './worker.service';
import { DismissCardAlertResponse } from './worker-cards.model';

describe('WorkerService', () => {
  let service: WorkerService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(WorkerService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('GETs the worker list from /api/heartbeat/', () => {
    service.list().subscribe(workers => expect(workers).toEqual([]));
    const req = httpMock.expectOne('/api/heartbeat/');
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('POSTs a dismissal with the alert id in the body only', () => {
    const alertId = 'b'.repeat(32);
    const answer: DismissCardAlertResponse = { card_status: null, card_alert_dismissals: [alertId] };
    let received: DismissCardAlertResponse | undefined;
    service.dismissCardAlert(7, alertId).subscribe(body => { received = body; });

    const req = httpMock.expectOne('/api/heartbeat/7/dismiss-card-alert/');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ alert_id: alertId });
    expect(req.request.url).not.toContain(alertId);
    req.flush(answer);

    expect(received).toEqual(answer);
  });
});
