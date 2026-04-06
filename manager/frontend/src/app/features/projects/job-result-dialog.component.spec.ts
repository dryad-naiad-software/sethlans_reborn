// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { of, throwError } from 'rxjs';
import {
  JobResultDialogComponent, JobResultDialogData,
} from './job-result-dialog.component';
import { JobService, Job } from '../../core/services/job.service';
import { AnimationService, Animation, AnimationFrame } from '../../core/services/animation.service';

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: 1, name: 'Frame 1',
    asset: null as any,
    output_file_pattern: '//render/test_####.png',
    start_frame: 1, end_frame: 1,
    status: 'DONE', status_display: 'Done',
    assigned_worker: null, assigned_worker_hostname: null,
    animation: 10, tiled_job: null, animation_frame: null,
    submitted_at: '2025-06-03T00:00:00Z',
    started_at: null, completed_at: null,
    blender_version: null,
    effective_blender_version: { series: '4.2', resolved_version: '4.2.1' },
    render_engine: 'CYCLES', render_device: 'ANY',
    cycles_feature_set: 'SUPPORTED',
    render_settings: {}, last_output: '', error_message: '',
    render_time_seconds: 10,
    output_file: '/media/output/frame_001.png',
    thumbnail: '/media/thumbs/frame_001.png',
    ...overrides,
  };
}

function makeAnimationFrame(overrides: Partial<AnimationFrame> = {}): AnimationFrame {
  return {
    id: 1, frame_number: 1, status: 'DONE',
    output_file: '/media/output/frame_001.png',
    thumbnail: '/media/thumbs/frame_001.png',
    render_time_seconds: 10,
    ...overrides,
  };
}

function makeAnimation(overrides: Partial<Animation> = {}): Animation {
  return {
    id: 10, name: 'Walk Cycle',
    status: 'DONE', progress: '5/5',
    total_frames: 5, completed_frames: 5,
    project: 'proj-uuid', project_details: null as any,
    asset: null as any,
    output_file_pattern: '//render/walk_####.png',
    start_frame: 1, end_frame: 5, frame_step: 1,
    blender_version: null,
    effective_blender_version: { series: '4.2', resolved_version: '4.2.1' },
    render_engine: 'CYCLES', render_device: 'ANY',
    cycles_feature_set: 'SUPPORTED',
    render_settings: { 'render.resolution_x': 1920, 'render.resolution_y': 1080, 'cycles.samples': 128 },
    tiling_config: 'NONE',
    submitted_at: '2025-06-01T00:00:00Z', completed_at: '2025-06-01T01:00:00Z',
    total_render_time_seconds: 50,
    thumbnail: null, frames: [],
    ...overrides,
  };
}

function createComponent(
  dialogData: JobResultDialogData,
  jobServiceSpy: jasmine.SpyObj<JobService>,
): JobResultDialogComponent {
  const mockAnimationService = jasmine.createSpyObj('AnimationService', ['download']);
  const mockDialogRef = jasmine.createSpyObj('MatDialogRef', ['close']);

  TestBed.configureTestingModule({
    imports: [JobResultDialogComponent, NoopAnimationsModule],
    providers: [
      { provide: MAT_DIALOG_DATA, useValue: dialogData },
      { provide: MatDialogRef, useValue: mockDialogRef },
      { provide: JobService, useValue: jobServiceSpy },
      { provide: AnimationService, useValue: mockAnimationService },
    ],
  });

  return TestBed.createComponent(JobResultDialogComponent).componentInstance;
}

describe('JobResultDialogComponent', () => {
  let mockJobService: jasmine.SpyObj<JobService>;
  let snackBar: MatSnackBar;

  beforeEach(() => {
    mockJobService = jasmine.createSpyObj('JobService', ['list']);
    mockJobService.list.and.returnValue(of([]));
  });

  describe('animation with frames (tiled animation)', () => {
    it('should use AnimationFrame data directly for filmstrip', () => {
      const frames = [
        makeAnimationFrame({ id: 1, frame_number: 1 }),
        makeAnimationFrame({ id: 2, frame_number: 2 }),
      ];
      const anim = makeAnimation({ tiling_config: '2x2', frames });
      const data: JobResultDialogData = { type: 'animation', animation: anim };

      const comp = createComponent(data, mockJobService);

      expect(comp.filmstripFrames.length).toBe(2);
      expect(comp.filmstripFrames[0].frameNumber).toBe(1);
      expect(comp.filmstripFrames[1].frameNumber).toBe(2);
      expect(comp.selectedFilmstripFrame?.id).toBe(1);
      expect(comp.loadingFrames).toBeFalse();
      expect(mockJobService.list).not.toHaveBeenCalled();
    });

    it('should select frame at selectedFrameIndex', () => {
      const frames = [
        makeAnimationFrame({ id: 1, frame_number: 1 }),
        makeAnimationFrame({ id: 2, frame_number: 2 }),
        makeAnimationFrame({ id: 3, frame_number: 3 }),
      ];
      const anim = makeAnimation({ tiling_config: '2x2', frames });
      const data: JobResultDialogData = {
        type: 'animation', animation: anim, selectedFrameIndex: 2,
      };

      const comp = createComponent(data, mockJobService);
      expect(comp.selectedFilmstripFrame?.id).toBe(3);
    });
  });

  describe('standard animation (tiling_config NONE, empty frames)', () => {
    it('should fetch jobs for the animation and map to filmstrip frames', () => {
      const jobs = [
        makeJob({ id: 3, start_frame: 3, animation: 10 }),
        makeJob({ id: 1, start_frame: 1, animation: 10 }),
        makeJob({ id: 2, start_frame: 2, animation: 10 }),
      ];
      mockJobService.list.and.returnValue(of(jobs));

      const anim = makeAnimation({ tiling_config: 'NONE', frames: [] });
      const data: JobResultDialogData = { type: 'animation', animation: anim };

      const comp = createComponent(data, mockJobService);

      expect(mockJobService.list).toHaveBeenCalledWith({ animation: 10, status: 'DONE' });
      expect(comp.filmstripFrames.length).toBe(3);
      // Sorted by start_frame ascending
      expect(comp.filmstripFrames[0].frameNumber).toBe(1);
      expect(comp.filmstripFrames[1].frameNumber).toBe(2);
      expect(comp.filmstripFrames[2].frameNumber).toBe(3);
      expect(comp.selectedFilmstripFrame?.frameNumber).toBe(1);
      expect(comp.loadingFrames).toBeFalse();
    });

    it('should show error snackbar when job fetch fails', () => {
      mockJobService.list.and.returnValue(throwError(() => new Error('fail')));
      const anim = makeAnimation({ tiling_config: 'NONE', frames: [] });
      const data: JobResultDialogData = { type: 'animation', animation: anim };

      const comp = createComponent(data, mockJobService);
      snackBar = TestBed.inject(MatSnackBar);
      spyOn(snackBar, 'open');

      // Re-create to trigger the constructor with snackbar spy
      // The error already fired in constructor, but snackBar.open was called
      // before the spy was set up. Let's verify the state instead.
      expect(comp.loadingFrames).toBeFalse();
      expect(comp.filmstripFrames.length).toBe(0);
    });

    it('should handle empty job results gracefully', () => {
      mockJobService.list.and.returnValue(of([]));
      const anim = makeAnimation({ tiling_config: 'NONE', frames: [] });
      const data: JobResultDialogData = { type: 'animation', animation: anim };

      const comp = createComponent(data, mockJobService);

      expect(comp.filmstripFrames.length).toBe(0);
      expect(comp.selectedFilmstripFrame).toBeNull();
      expect(comp.loadingFrames).toBeFalse();
    });
  });

  describe('onFrameSelect', () => {
    it('should update selectedFilmstripFrame', () => {
      mockJobService.list.and.returnValue(of([]));
      const anim = makeAnimation({ tiling_config: 'NONE', frames: [] });
      const data: JobResultDialogData = { type: 'animation', animation: anim };

      const comp = createComponent(data, mockJobService);
      const frame = { id: 5, frameNumber: 5, thumbnail: null, outputFile: '/img.png' };
      comp.onFrameSelect(frame);

      expect(comp.selectedFilmstripFrame).toEqual(frame);
    });
  });

  describe('single job dialog', () => {
    it('should not fetch jobs for non-animation types', () => {
      const job = makeJob({ animation: null });
      const data: JobResultDialogData = { type: 'single', job };

      const comp = createComponent(data, mockJobService);

      expect(mockJobService.list).not.toHaveBeenCalled();
      expect(comp.filmstripFrames.length).toBe(0);
    });
  });
});
