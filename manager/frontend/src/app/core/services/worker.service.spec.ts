// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { WorkerService } from './worker.service';
import { CardChoiceBody, CardChoiceSendResponse, DismissCardAlertResponse } from './worker-cards.model';

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

  it('POSTs a card change as JSON to the card-choice action', () => {
    const body: CardChoiceBody = {
      method: 'gpu', gpu_mode: 'split',
      cards: [{ key: 'pci:0000:01:00.0', vendor_id: 0x10de, device_id: 0x2b85 }],
      shown: ['pci:0000:01:00.0'], base_revision: 3,
    };
    const answer: CardChoiceSendResponse = {
      card_choice_request: {
        ...body, id: 'd'.repeat(32), requested_by: 'user id=5', requested_at: '2026-11-19T10:00:00Z',
      },
      replaced: null,
    };
    let received: CardChoiceSendResponse | undefined;
    service.sendCardChoice(7, body).subscribe(b => { received = b; });

    const req = httpMock.expectOne('/api/heartbeat/7/card-choice/');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(body);
    req.flush(answer, { status: 202, statusText: 'Accepted' });
    expect(received).toEqual(answer);
  });

  it('DELETEs the pending change without a body', () => {
    let done = false;
    service.withdrawCardChoice(7).subscribe(() => { done = true; });

    const req = httpMock.expectOne('/api/heartbeat/7/card-choice/');
    expect(req.request.method).toBe('DELETE');
    expect(req.request.body).toBeNull();
    req.flush(null, { status: 204, statusText: 'No Content' });
    expect(done).toBeTrue();
  });
});
