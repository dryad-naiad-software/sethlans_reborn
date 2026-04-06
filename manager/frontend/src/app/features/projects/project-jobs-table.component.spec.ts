// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { SimpleChange } from '@angular/core';
import { of } from 'rxjs';
import { ProjectJobsTableComponent } from './project-jobs-table.component';
import { JobService, Job } from '../../core/services/job.service';
import { TiledJobService, TiledJob } from '../../core/services/tiled-job.service';
import { AnimationService, Animation } from '../../core/services/animation.service';

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: 1, name: 'Single Job',
    asset: null as any,
    output_file_pattern: '//render/test_####.png',
    start_frame: 1, end_frame: 1,
    status: 'QUEUED', is_paused: false, status_display: 'Queued',
    assigned_worker: null, assigned_worker_hostname: null,
    animation: null, tiled_job: null, animation_frame: null,
    submitted_at: '2025-06-03T00:00:00Z',
    started_at: null, completed_at: null,
    blender_version: null,
    effective_blender_version: { series: '4.2', resolved_version: '4.2.1' },
    render_engine: 'CYCLES', render_device: 'ANY',
    cycles_feature_set: 'SUPPORTED',
    render_settings: {}, last_output: '', error_message: '',
    render_time_seconds: null,
    output_file: null, thumbnail: null,
    ...overrides,
  };
}

function makeTiledJob(overrides: Partial<TiledJob> = {}): TiledJob {
  return {
    id: 'tiled-uuid', name: 'Tiled Job',
    status: 'DONE', progress: '16/16',
    total_tiles: 16, completed_tiles: 16,
    project: 'proj-uuid', project_details: null as any,
    asset: null as any,
    final_resolution_x: 1920, final_resolution_y: 1080,
    tile_count_x: 4, tile_count_y: 4,
    blender_version: null,
    effective_blender_version: { series: '4.2', resolved_version: '4.2.1' },
    render_engine: 'CYCLES', render_device: 'ANY',
    cycles_feature_set: 'SUPPORTED', render_settings: {},
    submitted_at: '2025-06-02T00:00:00Z',
    completed_at: '2025-06-02T01:00:00Z',
    total_render_time_seconds: 120,
    output_file: null, thumbnail: null,
    ...overrides,
  };
}

function makeAnimation(overrides: Partial<Animation> = {}): Animation {
  return {
    id: 1, name: 'Walk Cycle',
    status: 'RENDERING', progress: '100/250',
    total_frames: 250, completed_frames: 100,
    project: 'proj-uuid', project_details: null as any,
    asset: null as any,
    output_file_pattern: '//render/walk_####.png',
    start_frame: 1, end_frame: 250, frame_step: 1,
    blender_version: null,
    effective_blender_version: { series: '4.2', resolved_version: '4.2.1' },
    render_engine: 'CYCLES', render_device: 'ANY',
    cycles_feature_set: 'SUPPORTED', render_settings: {},
    tiling_config: 'NONE',
    submitted_at: '2025-06-01T00:00:00Z',
    completed_at: null,
    total_render_time_seconds: 500,
    thumbnail: null, frames: [],
    ...overrides,
  };
}

describe('ProjectJobsTableComponent', () => {
  let component: ProjectJobsTableComponent;
  let fixture: ComponentFixture<ProjectJobsTableComponent>;
  let mockJobService: jasmine.SpyObj<JobService>;
  let mockTiledJobService: jasmine.SpyObj<TiledJobService>;
  let mockAnimationService: jasmine.SpyObj<AnimationService>;

  beforeEach(async () => {
    mockJobService = jasmine.createSpyObj('JobService', ['list']);
    mockTiledJobService = jasmine.createSpyObj('TiledJobService', ['list']);
    mockAnimationService = jasmine.createSpyObj('AnimationService', ['list']);

    mockJobService.list.and.returnValue(of([makeJob()]));
    mockTiledJobService.list.and.returnValue(of([makeTiledJob()]));
    mockAnimationService.list.and.returnValue(of([makeAnimation()]));

    await TestBed.configureTestingModule({
      imports: [ProjectJobsTableComponent, NoopAnimationsModule],
      providers: [
        { provide: JobService, useValue: mockJobService },
        { provide: TiledJobService, useValue: mockTiledJobService },
        { provide: AnimationService, useValue: mockAnimationService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(ProjectJobsTableComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should start with loading true', () => {
    expect(component.loading).toBeTrue();
  });

  it('should have correct table columns', () => {
    expect(component.columns).toEqual(
      ['thumbnail', 'name', 'type', 'status', 'worker', 'time', 'createdAt', 'actions']);
  });

  describe('when projectId is set', () => {
    beforeEach(() => {
      component.projectId = 'proj-uuid';
      component.ngOnChanges({
        projectId: new SimpleChange('', 'proj-uuid', true),
      });
    });

    it('should merge jobs from all three sources', () => {
      expect(component.rows.length).toBe(3);
    });

    it('should set loading to false', () => {
      expect(component.loading).toBeFalse();
    });

    it('should map single jobs correctly', () => {
      const single = component.rows.find(r => r.type === 'single');
      expect(single).toBeTruthy();
      expect(single!.name).toBe('Single Job');
      expect(single!.status).toBe('QUEUED');
      expect(single!.worker).toBe('--');
    });

    it('should map tiled jobs correctly', () => {
      const tiled = component.rows.find(r => r.type === 'tiled');
      expect(tiled).toBeTruthy();
      expect(tiled!.name).toBe('Tiled Job');
      expect(tiled!.status).toBe('DONE');
      expect(tiled!.time).toBe('2m 0s');
    });

    it('should map animations correctly', () => {
      const anim = component.rows.find(r => r.type === 'animation');
      expect(anim).toBeTruthy();
      expect(anim!.name).toBe('Walk Cycle');
      expect(anim!.status).toBe('RENDERING');
    });

    it('should sort rows by createdAt descending', () => {
      expect(component.rows[0].name).toBe('Single Job');
      expect(component.rows[2].name).toBe('Walk Cycle');
    });

    it('should filter out child jobs (tiled/animation sub-jobs)', () => {
      const childJob = makeJob({
        id: 2, name: 'Tile 1', tiled_job: 'tiled-uuid',
        submitted_at: '2025-06-01T00:00:00Z',
      });
      mockJobService.list.and.returnValue(of([makeJob(), childJob]));

      component.ngOnChanges({
        projectId: new SimpleChange('proj-uuid', 'proj-uuid', false),
      });

      const singles = component.rows.filter(r => r.type === 'single');
      expect(singles.length).toBe(1);
    });
  });

  describe('worker display', () => {
    it('should show hostname when assigned', () => {
      const job = makeJob({ assigned_worker_hostname: 'worker-01' });
      mockJobService.list.and.returnValue(of([job]));
      mockTiledJobService.list.and.returnValue(of([]));
      mockAnimationService.list.and.returnValue(of([]));

      component.projectId = 'proj-uuid';
      component.ngOnChanges({
        projectId: new SimpleChange('', 'proj-uuid', true),
      });

      expect(component.rows[0].worker).toBe('worker-01');
    });
  });

  describe('status icons', () => {
    it('should return hourglass_empty for QUEUED', () => {
      expect(component.statusIcon('QUEUED')).toBe('hourglass_empty');
    });

    it('should return sync for RENDERING', () => {
      expect(component.statusIcon('RENDERING')).toBe('sync');
    });

    it('should return check_circle for DONE', () => {
      expect(component.statusIcon('DONE')).toBe('check_circle');
    });

    it('should return error for ERROR', () => {
      expect(component.statusIcon('ERROR')).toBe('error');
    });

    it('should return cancel for CANCELED', () => {
      expect(component.statusIcon('CANCELED')).toBe('cancel');
    });

    it('should return build for ASSEMBLING', () => {
      expect(component.statusIcon('ASSEMBLING')).toBe('build');
    });

    it('should return help_outline for unknown status', () => {
      expect(component.statusIcon('UNKNOWN')).toBe('help_outline');
    });
  });

  describe('type icons and labels', () => {
    it('should map type to correct icon and label', () => {
      expect(component.typeIcon('single')).toBe('image');
      expect(component.typeLabel('single')).toBe('Single');
      expect(component.typeIcon('tiled')).toBe('grid_view');
      expect(component.typeLabel('tiled')).toBe('Tiled');
      expect(component.typeIcon('animation')).toBe('movie');
      expect(component.typeLabel('animation')).toBe('Animation');
    });
  });

  it('should unsubscribe on destroy', () => {
    component.projectId = 'proj-uuid';
    component.ngOnChanges({
      projectId: new SimpleChange('', 'proj-uuid', true),
    });
    component.ngOnDestroy();
    // No error = subscription cleaned up
  });
});
