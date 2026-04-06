// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TiledJobService, TiledJob, CreateTiledJobRequest } from './tiled-job.service';

const MOCK_TILED: TiledJob = {
  id: 'tiled-uuid-1', name: 'Tiled Render',
  status: 'QUEUED', progress: '0/16',
  total_tiles: 16, completed_tiles: 0,
  project: 'proj-uuid',
  project_details: null as any,
  asset: null as any,
  final_resolution_x: 1920, final_resolution_y: 1080,
  tile_count_x: 4, tile_count_y: 4,
  blender_version: null,
  effective_blender_version: { series: '4.2', resolved_version: '4.2.1' },
  render_engine: 'CYCLES', render_device: 'ANY',
  cycles_feature_set: 'SUPPORTED',
  render_settings: { 'cycles.samples': 128 },
  submitted_at: '2025-06-01T00:00:00Z',
  completed_at: null,
  total_render_time_seconds: 0,
  output_file: null, thumbnail: null,
};

describe('TiledJobService', () => {
  let service: TiledJobService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(TiledJobService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should GET /api/tiled-jobs/ on list() without filters', () => {
    service.list().subscribe(jobs => {
      expect(jobs).toEqual([MOCK_TILED]);
    });
    const req = httpMock.expectOne('/api/tiled-jobs/');
    expect(req.request.method).toBe('GET');
    req.flush([MOCK_TILED]);
  });

  it('should apply project filter on list()', () => {
    service.list({ project: 'proj-uuid' }).subscribe();
    const req = httpMock.expectOne(r =>
      r.url === '/api/tiled-jobs/' && r.params.get('project') === 'proj-uuid'
    );
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('should GET /api/tiled-jobs/{id}/ on get()', () => {
    service.get('tiled-uuid-1').subscribe(job => {
      expect(job.id).toBe('tiled-uuid-1');
    });
    const req = httpMock.expectOne('/api/tiled-jobs/tiled-uuid-1/');
    expect(req.request.method).toBe('GET');
    req.flush(MOCK_TILED);
  });

  it('should DELETE /api/tiled-jobs/{id}/ on delete()', () => {
    service.delete('tiled-uuid-1').subscribe();
    const req = httpMock.expectOne('/api/tiled-jobs/tiled-uuid-1/');
    expect(req.request.method).toBe('DELETE');
    req.flush(null);
  });

  it('should POST /api/tiled-jobs/ on create()', () => {
    const payload: CreateTiledJobRequest = {
      name: 'Tiled', project: 'proj-uuid', asset_id: 1,
      final_resolution_x: 1920, final_resolution_y: 1080,
      tile_count_x: 4, tile_count_y: 4,
      render_engine: 'CYCLES', render_device: 'ANY',
      render_settings: { 'cycles.samples': 128 },
    };
    service.create(payload).subscribe(job => {
      expect(job.name).toBe('Tiled Render');
    });
    const req = httpMock.expectOne('/api/tiled-jobs/');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(payload);
    req.flush(MOCK_TILED);
  });
});
