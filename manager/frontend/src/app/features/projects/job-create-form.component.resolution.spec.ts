// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatSnackBar } from '@angular/material/snack-bar';
import { MatDialogRef, MAT_DIALOG_DATA } from '@angular/material/dialog';
import { of } from 'rxjs';
import { JobCreateFormComponent } from './job-create-form.component';
import { JobService } from '../../core/services/job.service';
import { TiledJobService } from '../../core/services/tiled-job.service';
import { AnimationService } from '../../core/services/animation.service';

describe('JobCreateFormComponent — resolution preset coverage', () => {
  describe('resolution preset submission', () => {
    let component: JobCreateFormComponent;
    let mockJobService: jasmine.SpyObj<JobService>;
    let mockTiledJobService: jasmine.SpyObj<TiledJobService>;
    let mockAnimationService: jasmine.SpyObj<AnimationService>;
    let mockDialogRef: jasmine.SpyObj<MatDialogRef<JobCreateFormComponent>>;

    beforeEach(async () => {
      mockJobService = jasmine.createSpyObj('JobService', ['create']);
      mockTiledJobService = jasmine.createSpyObj('TiledJobService', ['create']);
      mockAnimationService = jasmine.createSpyObj('AnimationService', ['create']);
      mockDialogRef = jasmine.createSpyObj('MatDialogRef', ['close']);
      await TestBed.configureTestingModule({
        imports: [JobCreateFormComponent, NoopAnimationsModule],
        providers: [
          { provide: JobService, useValue: mockJobService },
          { provide: TiledJobService, useValue: mockTiledJobService },
          { provide: AnimationService, useValue: mockAnimationService },
          { provide: MatDialogRef, useValue: mockDialogRef },
          { provide: MAT_DIALOG_DATA, useValue: { projectId: 'proj-uuid', assetId: 42 } },
        ],
      }).compileComponents();

      const fixture = TestBed.createComponent(JobCreateFormComponent);
      component = fixture.componentInstance;
      const snackBar = fixture.debugElement.injector.get(MatSnackBar);
      spyOn(snackBar, 'open');
      fixture.detectChanges();
    });

    it('should submit Preset mode with uhd4k preset producing 3840x2160 in payload', () => {
      component.renderType = 'single';
      component.form.controls.name.setValue('Preset Render');
      component.form.controls.resolutionMode.setValue('preset');
      component.form.controls.resolutionPreset.setValue('uhd4k');
      mockJobService.create.and.returnValue(of({ name: 'Preset Render' } as any));

      component.onSubmit();

      const callArgs = mockJobService.create.calls.mostRecent().args[0] as any;
      expect(callArgs.render_settings['render.resolution_x']).toBe(3840);
      expect(callArgs.render_settings['render.resolution_y']).toBe(2160);
    });

    it('should submit Custom mode with user-typed 1600/900 producing those values in payload', () => {
      component.renderType = 'single';
      component.form.controls.name.setValue('Custom Render');
      component.form.controls.resolutionMode.setValue('custom');
      component.form.controls.resolutionX.setValue(1600);
      component.form.controls.resolutionY.setValue(900);
      mockJobService.create.and.returnValue(of({ name: 'Custom Render' } as any));

      component.onSubmit();

      const callArgs = mockJobService.create.calls.mostRecent().args[0] as any;
      expect(callArgs.render_settings['render.resolution_x']).toBe(1600);
      expect(callArgs.render_settings['render.resolution_y']).toBe(900);
    });
  });

  describe('dialog re-open with different prefill (S8)', () => {
    async function createFixtureWithPrefill(
      resolutionX: number, resolutionY: number,
    ): Promise<JobCreateFormComponent> {
      const localJobService = jasmine.createSpyObj<JobService>('JobService', ['create']);
      const localTiledJobService = jasmine.createSpyObj<TiledJobService>('TiledJobService', ['create']);
      const localAnimationService = jasmine.createSpyObj<AnimationService>('AnimationService', ['create']);
      const localDialogRef = jasmine.createSpyObj<MatDialogRef<JobCreateFormComponent>>(
        'MatDialogRef', ['close'],
      );
      TestBed.resetTestingModule();
      await TestBed.configureTestingModule({
        imports: [JobCreateFormComponent, NoopAnimationsModule],
        providers: [
          { provide: JobService, useValue: localJobService },
          { provide: TiledJobService, useValue: localTiledJobService },
          { provide: AnimationService, useValue: localAnimationService },
          { provide: MatDialogRef, useValue: localDialogRef },
          {
            provide: MAT_DIALOG_DATA,
            useValue: {
              projectId: 'proj-uuid',
              assetId: 42,
              prefill: { renderType: 'single', resolutionX, resolutionY },
            },
          },
        ],
      }).compileComponents();
      const localFixture = TestBed.createComponent(JobCreateFormComponent);
      const localSnackBar = localFixture.debugElement.injector.get(MatSnackBar);
      spyOn(localSnackBar, 'open');
      localFixture.detectChanges();
      return localFixture.componentInstance;
    }

    it('should detect fhd1080 preset on first open with 1920x1080 prefill', async () => {
      const c = await createFixtureWithPrefill(1920, 1080);
      expect(c.form.controls.resolutionMode.value).toBe('preset');
      expect(c.form.controls.resolutionPreset.value).toBe('fhd1080');
      expect(c.form.controls.resolutionX.value).toBe(1920);
      expect(c.form.controls.resolutionY.value).toBe(1080);
    });

    it('should detect uhd4k preset on second open with 3840x2160 prefill (fresh instance)', async () => {
      // First open with 1920x1080
      await createFixtureWithPrefill(1920, 1080);
      // Second open (fresh fixture) with 3840x2160
      const c2 = await createFixtureWithPrefill(3840, 2160);
      expect(c2.form.controls.resolutionMode.value).toBe('preset');
      expect(c2.form.controls.resolutionPreset.value).toBe('uhd4k');
      expect(c2.form.controls.resolutionX.value).toBe(3840);
      expect(c2.form.controls.resolutionY.value).toBe(2160);
    });
  });
});
