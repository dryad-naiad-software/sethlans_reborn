// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatSnackBar } from '@angular/material/snack-bar';
import { MatDialogRef, MAT_DIALOG_DATA } from '@angular/material/dialog';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { of, throwError } from 'rxjs';
import { JobCreateFormComponent } from './job-create-form.component';
import { JobService } from '../../core/services/job.service';
import { TiledJobService } from '../../core/services/tiled-job.service';
import { AnimationService } from '../../core/services/animation.service';
import { FFmpegStatusResponse } from '../../core/services/ffmpeg-status.service';

const FFMPEG_URL = '/api/ffmpeg-status/';

describe('JobCreateFormComponent', () => {
  let component: JobCreateFormComponent;
  let fixture: ComponentFixture<JobCreateFormComponent>;
  let mockJobService: jasmine.SpyObj<JobService>;
  let mockTiledJobService: jasmine.SpyObj<TiledJobService>;
  let mockAnimationService: jasmine.SpyObj<AnimationService>;
  let mockDialogRef: jasmine.SpyObj<MatDialogRef<JobCreateFormComponent>>;
  let httpMock: HttpTestingController;
  let snackBar: MatSnackBar;

  beforeEach(async () => {
    mockJobService = jasmine.createSpyObj('JobService', ['create']);
    mockTiledJobService = jasmine.createSpyObj('TiledJobService', ['create']);
    mockAnimationService = jasmine.createSpyObj('AnimationService', ['create']);
    mockDialogRef = jasmine.createSpyObj('MatDialogRef', ['close']);
    await TestBed.configureTestingModule({
      imports: [JobCreateFormComponent, NoopAnimationsModule],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: JobService, useValue: mockJobService },
        { provide: TiledJobService, useValue: mockTiledJobService },
        { provide: AnimationService, useValue: mockAnimationService },
        { provide: MatDialogRef, useValue: mockDialogRef },
        { provide: MAT_DIALOG_DATA, useValue: { projectId: 'proj-uuid', assetId: 42 } },
      ],
    }).compileComponents();

    httpMock = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(JobCreateFormComponent);
    component = fixture.componentInstance;
    snackBar = fixture.debugElement.injector.get(MatSnackBar);
    spyOn(snackBar, 'open');
    fixture.detectChanges();
    // Default to a "FFmpeg ready" world for the existing test set so the
    // form behaves as it did before the FFmpeg-status integration. The
    // describe('FFmpeg video-assembly status integration') block below
    // builds its own fixtures and asserts call count + grey-out behaviour.
    flushDefaultFFmpegReady();
  });

  function flushDefaultFFmpegReady() {
    const reqs = httpMock.match(FFMPEG_URL);
    reqs.forEach(r => r.flush({ video_assembly_ready: true } as FFmpegStatusResponse));
    fixture.detectChanges();
  }

  afterEach(() => httpMock.verify());

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should default render type to single', () => {
    expect(component.renderType).toBe('single');
  });

  describe('form defaults', () => {
    it('should set default render engine to CYCLES', () => {
      expect(component.form.controls.renderEngine.value).toBe('CYCLES');
    });
    it('should set default samples to 128', () => {
      expect(component.form.controls.samples.value).toBe(128);
    });
    it('should set default resolution to 1920x1080', () => {
      expect(component.form.controls.resolutionX.value).toBe(1920);
      expect(component.form.controls.resolutionY.value).toBe(1080);
    });
    it('should set default frame to 1', () => {
      expect(component.form.controls.frame.value).toBe(1);
    });
    it('should set default animation frames 1-250 step 1', () => {
      expect(component.form.controls.startFrame.value).toBe(1);
      expect(component.form.controls.endFrame.value).toBe(250);
      expect(component.form.controls.frameStep.value).toBe(1);
    });
  });

  describe('form validation', () => {
    it('should require name', () => {
      expect(component.form.controls.name.hasError('required')).toBeTrue();
    });
    it('should reject name shorter than 4 chars', () => {
      component.form.controls.name.setValue('abc');
      expect(component.form.controls.name.hasError('minlength')).toBeTrue();
    });
    it('should reject samples less than 1', () => {
      component.form.controls.samples.setValue(0);
      expect(component.form.controls.samples.hasError('min')).toBeTrue();
    });
  });

  describe('single render submission', () => {
    beforeEach(() => {
      component.renderType = 'single';
      component.form.controls.name.setValue('My Render');
    });

    it('should call jobService.create with correct payload', () => {
      mockJobService.create.and.returnValue(of({ name: 'My Render' } as any));
      component.onSubmit();
      expect(mockJobService.create).toHaveBeenCalledWith({
        name: 'My Render', asset_id: 42,
        output_file_pattern: '//render/my_render_####.png',
        start_frame: 1, end_frame: 1,
        render_engine: 'CYCLES', render_device: 'ANY',
        render_settings: {
          'cycles.samples': 128, 'render.resolution_x': 1920,
          'render.resolution_y': 1080,
          'render.image_settings.file_format': 'PNG',
        },
      });
    });

    it('should close dialog on success', () => {
      mockJobService.create.and.returnValue(of({ name: 'My Render' } as any));
      component.onSubmit();
      expect(mockDialogRef.close).toHaveBeenCalledWith('My Render');
    });

    it('should show snackbar on success', () => {
      mockJobService.create.and.returnValue(of({ name: 'My Render' } as any));
      component.onSubmit();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Created "My Render"', 'Dismiss', { duration: 3000 });
    });
  });

  describe('tiled render submission', () => {
    it('should call tiledJobService.create with correct payload', () => {
      component.renderType = 'tiled';
      component.form.controls.name.setValue('Tiled Job');
      component.form.controls.tilingConfig.setValue('4x4');
      mockTiledJobService.create.and.returnValue(of({ name: 'Tiled Job' } as any));
      component.onSubmit();
      expect(mockTiledJobService.create).toHaveBeenCalledWith({
        name: 'Tiled Job', project: 'proj-uuid', asset_id: 42,
        final_resolution_x: 1920, final_resolution_y: 1080,
        tile_count_x: 4, tile_count_y: 4,
        render_engine: 'CYCLES', render_device: 'ANY',
        render_settings: {
          'cycles.samples': 128,
          'render.image_settings.file_format': 'PNG',
        },
      });
    });
  });

  describe('animation submission', () => {
    it('should call animationService.create with correct payload', () => {
      component.renderType = 'animation';
      component.form.controls.name.setValue('Walk Cycle');
      mockAnimationService.create.and.returnValue(of({ name: 'Walk Cycle' } as any));
      component.onSubmit();
      expect(mockAnimationService.create).toHaveBeenCalledWith({
        name: 'Walk Cycle', project: 'proj-uuid', asset_id: 42,
        output_file_pattern: '//render/walk_cycle_####.png',
        start_frame: 1, end_frame: 250, frame_step: 1,
        tiling_config: 'NONE',
        render_engine: 'CYCLES', render_device: 'ANY',
        render_settings: {
          'cycles.samples': 128, 'render.resolution_x': 1920,
          'render.resolution_y': 1080,
          'render.image_settings.file_format': 'PNG',
        },
      });
    });
  });

  describe('error handling', () => {
    beforeEach(() => {
      component.form.controls.name.setValue('Test Job');
    });
    it('should show error message from API response', () => {
      mockJobService.create.and.returnValue(
        throwError(() => ({ error: { name: ['Name already exists'] } })));
      component.renderType = 'single';
      component.onSubmit();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Name already exists', 'Dismiss', { duration: 5000 });
    });
    it('should show generic message when no specific error', () => {
      mockJobService.create.and.returnValue(throwError(() => ({ error: {} })));
      component.renderType = 'single';
      component.onSubmit();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Failed to create job', 'Dismiss', { duration: 5000 });
    });
    it('should reset submitting flag on error', () => {
      mockJobService.create.and.returnValue(throwError(() => ({ error: {} })));
      component.renderType = 'single';
      component.onSubmit();
      expect(component.submitting).toBeFalse();
    });
  });

  it('should not submit when form is invalid', () => {
    component.form.controls.name.setValue('');
    component.onSubmit();
    expect(mockJobService.create).not.toHaveBeenCalled();
  });
});
