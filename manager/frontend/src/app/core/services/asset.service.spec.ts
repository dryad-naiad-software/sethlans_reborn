// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { HttpEventType } from '@angular/common/http';
import { AssetService, Asset } from './asset.service';

const MOCK_ASSET: Asset = {
  id: 1, name: 'scene.blend', blend_file: '/media/assets/scene.blend',
  created_at: '2025-06-01T00:00:00Z', project: 'abc-123-uuid',
  project_details: {
    id: 'abc-123-uuid', name: 'Test', blender_version: 1,
    blender_version_details: {
      id: 1, major: 4, minor: 2, series: '4.2',
      resolved_version: '4.2.1', is_default: true,
      added_at: '2025-01-01T00:00:00Z', last_patch_check: null,
    },
    created_at: '2025-06-01T00:00:00Z',
  },
};

describe('AssetService', () => {
  let service: AssetService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(AssetService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should GET /api/assets/ on list() without filters', () => {
    service.list().subscribe(assets => {
      expect(assets).toEqual([MOCK_ASSET]);
    });
    const req = httpMock.expectOne('/api/assets/');
    expect(req.request.method).toBe('GET');
    req.flush([MOCK_ASSET]);
  });

  it('should GET /api/assets/?project=... on list() with project filter', () => {
    service.list({ project: 'abc-123-uuid' }).subscribe();
    const req = httpMock.expectOne(r =>
      r.url === '/api/assets/' && r.params.get('project') === 'abc-123-uuid'
    );
    expect(req.request.method).toBe('GET');
    req.flush([MOCK_ASSET]);
  });

  it('should POST multipart FormData on upload()', () => {
    const file = new File(['data'], 'scene.blend', { type: 'application/octet-stream' });
    const events: number[] = [];

    service.upload('abc-123-uuid', 'scene.blend', file).subscribe(event => {
      if (event.type === HttpEventType.Response) {
        events.push(event.type);
      }
    });

    const req = httpMock.expectOne('/api/assets/');
    expect(req.request.method).toBe('POST');
    expect(req.request.body instanceof FormData).toBeTrue();
    req.flush(MOCK_ASSET);
  });

  it('should send project, name, and blend_file in FormData', () => {
    const file = new File(['data'], 'test.blend');
    service.upload('proj-uuid', 'test.blend', file).subscribe();

    const req = httpMock.expectOne('/api/assets/');
    const body = req.request.body as FormData;
    expect(body.get('project')).toBe('proj-uuid');
    expect(body.get('name')).toBe('test.blend');
    expect(body.get('blend_file')).toBeTruthy();
    req.flush(MOCK_ASSET);
  });
});
