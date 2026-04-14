// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { SetupApiService } from './setup-api.service';
import { SetupStateService } from './setup-state.service';

describe('SetupApiService', () => {
  let service: SetupApiService;
  let httpMock: HttpTestingController;
  let stateService: SetupStateService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(SetupApiService);
    httpMock = TestBed.inject(HttpTestingController);
    stateService = TestBed.inject(SetupStateService);
  });

  afterEach(() => httpMock.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('GET endpoints (no token header)', () => {
    it('should GET /api/setup/status/', () => {
      const mockStatus = {
        complete: false, topology: null,
        current_step: null, checkpoints: [],
      };
      service.getStatus().subscribe(res => {
        expect(res).toEqual(mockStatus);
      });
      const req = httpMock.expectOne('/api/setup/status/');
      expect(req.request.method).toBe('GET');
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      req.flush(mockStatus);
    });

    it('should GET ffmpeg progress', () => {
      service.getFfmpegProgress('task-1').subscribe();
      const req = httpMock.expectOne('/api/setup/ffmpeg/progress/task-1/');
      expect(req.request.method).toBe('GET');
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      req.flush({ status: 'downloading', percent: 50, error: null });
    });

    it('should GET blender progress', () => {
      service.getBlenderProgress('task-2').subscribe();
      const req = httpMock.expectOne('/api/setup/blender/progress/task-2/');
      expect(req.request.method).toBe('GET');
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      req.flush({ status: 'extracting', percent: 80, error: null });
    });

    it('should GET summary', () => {
      const mockSummary = {
        manager_url: 'https://localhost:8080',
        admin_username: 'admin',
        enrollment_key: 'ABC123',
        cert_fingerprint: 'AA:BB:CC',
        topology: 'manager',
      };
      service.getSummary().subscribe(res => {
        expect(res).toEqual(mockSummary);
      });
      const req = httpMock.expectOne('/api/setup/summary/');
      expect(req.request.method).toBe('GET');
      req.flush(mockSummary);
    });
  });

  describe('POST endpoints with token', () => {
    beforeEach(() => {
      stateService.setSetupToken('test-token-123');
    });

    it('should POST topology with token header', () => {
      service.setTopology({ topology: 'manager' }).subscribe();
      const req = httpMock.expectOne('/api/setup/topology/');
      expect(req.request.method).toBe('POST');
      expect(req.request.headers.get('X-Setup-Token')).toBe('test-token-123');
      expect(req.request.body).toEqual({ topology: 'manager' });
      req.flush({ status: 'ok' });
    });

    it('should POST network with token header', () => {
      const body = { bind_host: '0.0.0.0', bind_port: 8080 };
      service.configureNetwork(body).subscribe();
      const req = httpMock.expectOne('/api/setup/network/');
      expect(req.request.method).toBe('POST');
      expect(req.request.headers.get('X-Setup-Token')).toBe('test-token-123');
      expect(req.request.body).toEqual(body);
      req.flush({ status: 'ok', bind_host: '0.0.0.0', bind_port: 8080 });
    });

    it('should POST database with token header', () => {
      service.configureDatabase({ engine: 'sqlite' }).subscribe();
      const req = httpMock.expectOne('/api/setup/database/');
      expect(req.request.method).toBe('POST');
      expect(req.request.headers.get('X-Setup-Token')).toBe('test-token-123');
      req.flush({ status: 'ok' });
    });

    it('should POST admin-user with token header', () => {
      const body = {
        username: 'admin', email: 'a@b.com',
        password: 'pass1234', password_confirm: 'pass1234',
      };
      service.createAdminUser(body).subscribe();
      const req = httpMock.expectOne('/api/setup/admin-user/');
      expect(req.request.method).toBe('POST');
      expect(req.request.headers.get('X-Setup-Token')).toBe('test-token-123');
      req.flush({ status: 'ok', username: 'admin' });
    });

    it('should POST worker-password with token header', () => {
      service.setWorkerPassword({ password: 'secret' }).subscribe();
      const req = httpMock.expectOne('/api/setup/worker-password/');
      expect(req.request.method).toBe('POST');
      expect(req.request.headers.get('X-Setup-Token')).toBe('test-token-123');
      req.flush({ status: 'ok' });
    });

    it('should POST ffmpeg start with token header', () => {
      service.startFfmpegDownload().subscribe();
      const req = httpMock.expectOne('/api/setup/ffmpeg/start/');
      expect(req.request.method).toBe('POST');
      expect(req.request.headers.get('X-Setup-Token')).toBe('test-token-123');
      req.flush({ status: 'started', task_id: 'task-1' });
    });

    it('should POST ffmpeg cancel with token header', () => {
      service.cancelFfmpegDownload().subscribe();
      const req = httpMock.expectOne('/api/setup/ffmpeg/cancel/');
      expect(req.request.method).toBe('POST');
      expect(req.request.headers.get('X-Setup-Token')).toBe('test-token-123');
      req.flush({ status: 'cancelled' });
    });

    it('should POST blender start with token header', () => {
      service.startBlenderDownload().subscribe();
      const req = httpMock.expectOne('/api/setup/blender/start/');
      expect(req.request.method).toBe('POST');
      expect(req.request.headers.get('X-Setup-Token')).toBe('test-token-123');
      req.flush({ status: 'started', task_id: 'task-2' });
    });

    it('should POST blender cancel with token header', () => {
      service.cancelBlenderDownload().subscribe();
      const req = httpMock.expectOne('/api/setup/blender/cancel/');
      expect(req.request.method).toBe('POST');
      expect(req.request.headers.get('X-Setup-Token')).toBe('test-token-123');
      req.flush({ status: 'cancelled' });
    });

    it('should POST verify with token header', () => {
      service.verify().subscribe();
      const req = httpMock.expectOne('/api/setup/verify/');
      expect(req.request.method).toBe('POST');
      expect(req.request.headers.get('X-Setup-Token')).toBe('test-token-123');
      req.flush({ checks: [], all_passed: true });
    });
  });

  describe('POST endpoints without token', () => {
    it('should POST without X-Setup-Token when no token is set', () => {
      service.setTopology({ topology: 'manager' }).subscribe();
      const req = httpMock.expectOne('/api/setup/topology/');
      expect(req.request.headers.has('X-Setup-Token')).toBeFalse();
      req.flush({ status: 'ok' });
    });
  });
});
