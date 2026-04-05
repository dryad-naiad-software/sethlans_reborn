// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { JobService, Job, CreateJobRequest } from './job.service';

const MOCK_JOB: Job = {
  id: 1, name: 'Test Job',
  asset: { id: 1, name: 'scene.blend', blend_file: '/media/scene.blend',
    created_at: '2025-06-01T00:00:00Z', project: 'abc-uuid',
    project_details: null as any },
  output_file_pattern: '//render/test_####.png',
  start_frame: 1, end_frame: 1,
  status: 'QUEUED', status_display: 'Queued',
  assigned_worker: null, assigned_worker_hostname: null,
  animation: null, tiled_job: null, animation_frame: null,
  submitted_at: '2025-06-01T00:00:00Z',
  started_at: null, completed_at: null,
  blender_version: null,
  effective_blender_version: { series: '4.2', resolved_version: '4.2.1' },
  render_engine: 'CYCLES', render_device: 'ANY',
  cycles_feature_set: 'SUPPORTED',
  render_settings: { 'cycles.samples': 128 },
  last_output: '', error_message: '',
  render_time_seconds: null,
  output_file: null, thumbnail: null,
};

describe('JobService', () => {
  let service: JobService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(JobService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should GET /api/jobs/ on list() without filters', () => {
    service.list().subscribe(jobs => {
      expect(jobs.length).toBe(1);
    });
    const req = httpMock.expectOne('/api/jobs/');
    expect(req.request.method).toBe('GET');
    req.flush([MOCK_JOB]);
  });

  it('should apply asset__project filter on list()', () => {
    service.list({ asset__project: 'proj-uuid' }).subscribe();
    const req = httpMock.expectOne(r =>
      r.url === '/api/jobs/' && r.params.get('asset__project') === 'proj-uuid'
    );
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('should apply status filter on list()', () => {
    service.list({ status: 'DONE' }).subscribe();
    const req = httpMock.expectOne(r =>
      r.url === '/api/jobs/' && r.params.get('status') === 'DONE'
    );
    req.flush([]);
  });

  it('should GET /api/jobs/{id}/ on get()', () => {
    service.get(1).subscribe(job => {
      expect(job.id).toBe(1);
    });
    const req = httpMock.expectOne('/api/jobs/1/');
    expect(req.request.method).toBe('GET');
    req.flush(MOCK_JOB);
  });

  it('should POST /api/jobs/ on create()', () => {
    const payload: CreateJobRequest = {
      name: 'New Job', asset_id: 1,
      output_file_pattern: '//render/new_job_####.png',
      start_frame: 1, end_frame: 1,
      render_engine: 'CYCLES', render_device: 'ANY',
      render_settings: { 'cycles.samples': 128,
        'render.resolution_x': 1920, 'render.resolution_y': 1080 },
    };
    service.create(payload).subscribe(job => {
      expect(job.name).toBe('Test Job');
    });
    const req = httpMock.expectOne('/api/jobs/');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(payload);
    req.flush(MOCK_JOB);
  });
});
