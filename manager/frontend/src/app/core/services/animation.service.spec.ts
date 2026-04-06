// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { AnimationService, Animation, CreateAnimationRequest } from './animation.service';

const MOCK_ANIM: Animation = {
  id: 1, name: 'Walk Cycle',
  status: 'QUEUED', progress: '0/250',
  total_frames: 250, completed_frames: 0,
  project: 'proj-uuid',
  project_details: null as any,
  asset: null as any,
  output_file_pattern: '//render/walk_cycle_####.png',
  start_frame: 1, end_frame: 250, frame_step: 1,
  blender_version: null,
  effective_blender_version: { series: '4.2', resolved_version: '4.2.1' },
  render_engine: 'CYCLES', render_device: 'ANY',
  cycles_feature_set: 'SUPPORTED',
  render_settings: { 'cycles.samples': 128,
    'render.resolution_x': 1920, 'render.resolution_y': 1080 },
  tiling_config: 'NONE',
  submitted_at: '2025-06-01T00:00:00Z',
  completed_at: null,
  total_render_time_seconds: 0,
  thumbnail: null,
  frames: [],
  video_settings: null,
  video_status: null,
  video_file: null,
  video_error: null,
};

describe('AnimationService', () => {
  let service: AnimationService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(AnimationService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should GET /api/animations/ on list() without filters', () => {
    service.list().subscribe(anims => {
      expect(anims).toEqual([MOCK_ANIM]);
    });
    const req = httpMock.expectOne('/api/animations/');
    expect(req.request.method).toBe('GET');
    req.flush([MOCK_ANIM]);
  });

  it('should apply project filter on list()', () => {
    service.list({ project: 'proj-uuid' }).subscribe();
    const req = httpMock.expectOne(r =>
      r.url === '/api/animations/' && r.params.get('project') === 'proj-uuid'
    );
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('should GET /api/animations/{id}/ on get()', () => {
    service.get(1).subscribe(anim => {
      expect(anim.id).toBe(1);
    });
    const req = httpMock.expectOne('/api/animations/1/');
    expect(req.request.method).toBe('GET');
    req.flush(MOCK_ANIM);
  });

  it('should DELETE /api/animations/{id}/ on delete()', () => {
    service.delete(1).subscribe();
    const req = httpMock.expectOne('/api/animations/1/');
    expect(req.request.method).toBe('DELETE');
    req.flush(null);
  });

  it('should POST /api/animations/ on create()', () => {
    const payload: CreateAnimationRequest = {
      name: 'Walk Cycle', project: 'proj-uuid', asset_id: 1,
      output_file_pattern: '//render/walk_cycle_####.png',
      start_frame: 1, end_frame: 250, frame_step: 1,
      tiling_config: 'NONE',
      render_engine: 'CYCLES', render_device: 'ANY',
      render_settings: { 'cycles.samples': 128,
        'render.resolution_x': 1920, 'render.resolution_y': 1080 },
    };
    service.create(payload).subscribe(anim => {
      expect(anim.name).toBe('Walk Cycle');
    });
    const req = httpMock.expectOne('/api/animations/');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(payload);
    req.flush(MOCK_ANIM);
  });
});
