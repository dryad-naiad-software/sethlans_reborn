// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import {
  FFmpegDetails,
  FFmpegStatusResponse,
  FFmpegStatusService,
} from './ffmpeg-status.service';

const URL = '/api/ffmpeg-status/';

describe('FFmpegStatusService', () => {
  let service: FFmpegStatusService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(FFmpegStatusService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('videoAssemblyReady() defaults to false before the response arrives', () => {
    expect(service.videoAssemblyReady()).toBeFalse();
    expect(service.details()).toBeUndefined();
  });

  it('issues GET /api/ffmpeg-status/ exactly once on first load()', () => {
    service.load().subscribe();
    const req = httpMock.expectOne(URL);
    expect(req.request.method).toBe('GET');
    req.flush({ video_assembly_ready: true } as FFmpegStatusResponse);
    httpMock.expectNone(URL);
  });

  it('flips videoAssemblyReady to true after a ready response', () => {
    service.load().subscribe();
    const req = httpMock.expectOne(URL);
    req.flush({ video_assembly_ready: true } as FFmpegStatusResponse);
    expect(service.videoAssemblyReady()).toBeTrue();
  });

  it('keeps videoAssemblyReady false when response says not ready', () => {
    service.load().subscribe();
    const req = httpMock.expectOne(URL);
    req.flush({ video_assembly_ready: false } as FFmpegStatusResponse);
    expect(service.videoAssemblyReady()).toBeFalse();
  });

  it('exposes details when ffmpeg block is present (admin payload)', () => {
    const details: FFmpegDetails = {
      source: 'system',
      version: '8.1',
      path: '/usr/local/bin/ffmpeg',
      status: 'ready',
      error: null,
    };
    service.load().subscribe();
    const req = httpMock.expectOne(URL);
    req.flush({
      video_assembly_ready: true,
      ffmpeg: details,
    } as FFmpegStatusResponse);
    expect(service.details()).toEqual(details);
  });

  it('leaves details() undefined when ffmpeg block is absent (regular user)', () => {
    service.load().subscribe();
    const req = httpMock.expectOne(URL);
    req.flush({ video_assembly_ready: true } as FFmpegStatusResponse);
    expect(service.details()).toBeUndefined();
  });

  describe('caching for the page lifetime', () => {
    it('does not issue a second HTTP request on a follow-up load()', () => {
      service.load().subscribe();
      const req = httpMock.expectOne(URL);
      req.flush({ video_assembly_ready: true } as FFmpegStatusResponse);

      // Second subscription — must be served from cache.
      let cached: FFmpegStatusResponse | undefined;
      service.load().subscribe(r => (cached = r));
      httpMock.expectNone(URL);
      expect(cached).toEqual({ video_assembly_ready: true });
    });

    it('signal reads do not trigger additional HTTP requests', () => {
      service.load().subscribe();
      const req = httpMock.expectOne(URL);
      req.flush({ video_assembly_ready: true } as FFmpegStatusResponse);

      // Repeated reads of the signals must not re-fetch.
      for (let i = 0; i < 5; i++) {
        expect(service.videoAssemblyReady()).toBeTrue();
        expect(service.details()).toBeUndefined();
      }
      httpMock.expectNone(URL);
    });

    it('shares the in-flight request across concurrent subscribers', () => {
      const seen: FFmpegStatusResponse[] = [];
      service.load().subscribe(r => seen.push(r));
      service.load().subscribe(r => seen.push(r));
      service.load().subscribe(r => seen.push(r));
      const req = httpMock.expectOne(URL);
      req.flush({ video_assembly_ready: true } as FFmpegStatusResponse);
      expect(seen.length).toBe(3);
      httpMock.expectNone(URL);
    });
  });
});
