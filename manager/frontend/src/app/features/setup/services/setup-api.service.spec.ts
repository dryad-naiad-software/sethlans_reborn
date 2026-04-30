// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { SetupApiService } from './setup-api.service';

describe('SetupApiService', () => {
  let service: SetupApiService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    sessionStorage.clear();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(SetupApiService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('GET endpoints', () => {
    it('GETs /api/setup/status/ without X-Setup-Token', () => {
      service.getStatus().subscribe();
      const req = httpMock.expectOne('/api/setup/status/');
      expect(req.request.method).toBe('GET');
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      req.flush({
        complete: false, topology: null, current_step: null, checkpoints: [],
      });
    });

    it('GETs blender progress without X-Setup-Token', () => {
      service.getBlenderProgress('task-2').subscribe();
      const req = httpMock.expectOne('/api/setup/blender/progress/task-2/');
      expect(req.request.method).toBe('GET');
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      req.flush({ status: 'extracting', percent: 80, error: null });
    });

    it('GETs summary without X-Setup-Token', () => {
      service.getSummary().subscribe();
      const req = httpMock.expectOne('/api/setup/summary/');
      expect(req.request.method).toBe('GET');
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      req.flush({
        manager_url: 'https://localhost:8080',
        admin_username: 'admin',
        enrollment_key: 'ABC',
        cert_fingerprint: 'AA:BB',
        topology: 'manager',
      });
    });
  });

  describe('POST endpoints', () => {
    it('POSTs topology without X-Setup-Token', () => {
      service.setTopology({ topology: 'manager' }).subscribe();
      const req = httpMock.expectOne('/api/setup/topology/');
      expect(req.request.method).toBe('POST');
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      expect(req.request.body).toEqual({ topology: 'manager' });
      req.flush({ status: 'ok' });
    });

    it('POSTs network without X-Setup-Token', () => {
      const body = { bind_host: '0.0.0.0', bind_port: 8080 };
      service.configureNetwork(body).subscribe();
      const req = httpMock.expectOne('/api/setup/network/');
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      req.flush({ status: 'ok', bind_host: '0.0.0.0', bind_port: 8080 });
    });

    it('POSTs database without X-Setup-Token', () => {
      service.configureDatabase({ engine: 'sqlite' }).subscribe();
      const req = httpMock.expectOne('/api/setup/database/');
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      req.flush({ status: 'ok' });
    });

    it('POSTs admin-user without X-Setup-Token', () => {
      service.createAdminUser({
        username: 'admin', email: 'a@b.com',
        password: 'pw12345', password_confirm: 'pw12345',
      }).subscribe();
      const req = httpMock.expectOne('/api/setup/admin-user/');
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      req.flush({ status: 'ok', username: 'admin' });
    });

    it('POSTs worker-password without X-Setup-Token', () => {
      service.setWorkerPassword({ password: 'secret' }).subscribe();
      const req = httpMock.expectOne('/api/setup/worker-password/');
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      req.flush({ status: 'ok' });
    });

    it('POSTs blender start without X-Setup-Token', () => {
      service.startBlenderDownload().subscribe();
      const req = httpMock.expectOne('/api/setup/blender/start/');
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      req.flush({ status: 'started', task_id: 'task-2' });
    });

    it('POSTs verify without X-Setup-Token', () => {
      service.verify().subscribe();
      const req = httpMock.expectOne('/api/setup/verify/');
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      req.flush({ checks: [], all_passed: true });
    });
  });

  describe('getHealth', () => {
    it('GETs /api/health/ and returns boot_id/setup_mode', () => {
      let result: { boot_id: string; setup_mode: boolean } | null = null;
      service.getHealth().subscribe(r => (result = r));
      const req = httpMock.expectOne('/api/health/');
      expect(req.request.method).toBe('GET');
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      req.flush({ boot_id: 'uuid-1', setup_mode: true });
      expect(result).toEqual({ boot_id: 'uuid-1', setup_mode: true } as never);
    });
  });

  describe('getSetupSession (FR-BE-1 probe)', () => {
    it('GETs /api/setup/session/ with no body and emits void on 204', () => {
      let emitted = false;
      service.getSetupSession().subscribe({ next: () => (emitted = true) });
      const req = httpMock.expectOne('/api/setup/session/');
      expect(req.request.method).toBe('GET');
      expect(req.request.body).toBeNull();
      req.flush(null, { status: 204, statusText: 'No Content' });
      expect(emitted).toBeTrue();
    });

    it('propagates a 403 setup_in_progress as HttpErrorResponse', () => {
      const captured: { status?: number; code?: string } = {};
      service.getSetupSession().subscribe({
        next: () => fail('should not emit'),
        error: (err: { status: number; error?: { error?: { code?: string } } }) => {
          captured.status = err.status;
          captured.code = err.error?.error?.code;
        },
      });
      const req = httpMock.expectOne('/api/setup/session/');
      req.flush(
        { error: { code: 'setup_in_progress', message: 'nope', details: {} } },
        { status: 403, statusText: 'Forbidden' },
      );
      expect(captured.status).toBe(403);
      expect(captured.code).toBe('setup_in_progress');
    });
  });

  describe('requestRestart', () => {
    it('POSTs /api/setup/restart/ with empty body', () => {
      service.requestRestart().subscribe();
      const req = httpMock.expectOne('/api/setup/restart/');
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual({});
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      req.flush(null, { status: 202, statusText: 'Accepted' });
    });
  });
});
