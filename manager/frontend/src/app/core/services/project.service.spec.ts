// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ProjectService, Project } from './project.service';

const MOCK_VERSION = {
  id: 1, major: 4, minor: 2, series: '4.2',
  resolved_version: '4.2.1', is_default: true,
  added_at: '2025-01-01T00:00:00Z', last_patch_check: null,
};

const MOCK_PROJECT: Project = {
  id: 'abc-123-uuid', name: 'Test Project', blender_version: 1,
  blender_version_details: MOCK_VERSION,
  created_at: '2025-06-01T00:00:00Z', is_paused: false,
};

describe('ProjectService', () => {
  let service: ProjectService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(ProjectService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should GET /api/projects/ on list()', () => {
    service.list().subscribe(projects => {
      expect(projects).toEqual([MOCK_PROJECT]);
    });
    const req = httpMock.expectOne('/api/projects/');
    expect(req.request.method).toBe('GET');
    req.flush([MOCK_PROJECT]);
  });

  it('should GET /api/projects/{id}/ on get()', () => {
    service.get('abc-123-uuid').subscribe(project => {
      expect(project.id).toBe('abc-123-uuid');
    });
    const req = httpMock.expectOne('/api/projects/abc-123-uuid/');
    expect(req.request.method).toBe('GET');
    req.flush(MOCK_PROJECT);
  });

  it('should POST /api/projects/ on create()', () => {
    const payload = { name: 'New Project', blender_version: 1 };
    service.create(payload).subscribe(project => {
      expect(project.name).toBe('Test Project');
    });
    const req = httpMock.expectOne('/api/projects/');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(payload);
    req.flush(MOCK_PROJECT);
  });

  it('should DELETE /api/projects/{id}/ on delete()', () => {
    service.delete('abc-123-uuid').subscribe();
    const req = httpMock.expectOne('/api/projects/abc-123-uuid/');
    expect(req.request.method).toBe('DELETE');
    req.flush(null);
  });

  it('should POST /api/projects/{id}/pause/ on pause()', () => {
    service.pause('abc-123-uuid').subscribe(p => {
      expect(p.is_paused).toBeTrue();
    });
    const req = httpMock.expectOne('/api/projects/abc-123-uuid/pause/');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({});
    req.flush({ ...MOCK_PROJECT, is_paused: true });
  });

  it('should POST /api/projects/{id}/unpause/ on unpause()', () => {
    service.unpause('abc-123-uuid').subscribe(p => {
      expect(p.is_paused).toBeFalse();
    });
    const req = httpMock.expectOne('/api/projects/abc-123-uuid/unpause/');
    expect(req.request.method).toBe('POST');
    req.flush(MOCK_PROJECT);
  });
});
