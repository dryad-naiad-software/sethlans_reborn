// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { BehaviorSubject, Subject, of, throwError } from 'rxjs';
import { JobDetailComponent } from './job-detail.component';
import { JobService, Job, EffectiveBlenderVersion } from '../../core/services/job.service';
import { Project } from '../../core/services/project.service';
import { Asset } from '../../core/services/asset.service';

/**
 * Issue #244: the "Cycles Feature Set" row only appears for Cycles jobs whose
 * Blender series is below 5.0. Eevee, Workbench, Blender 5.x and Unreal jobs
 * (no Blender version) hide it; the other detail rows always render.
 */

const PROJECT: Project = {
  id: 'project-uuid', name: 'Project', renderer: 'BLENDER',
  blender_version: 1,
  blender_version_details: {
    id: 1, major: 4, minor: 2, series: '4.2', resolved_version: '4.2.1',
    is_default: true, added_at: '2025-01-01T00:00:00Z', last_patch_check: null,
  },
  engine_build: '', created_at: '2025-06-01T00:00:00Z',
};

const ASSET: Asset = {
  id: 5, name: 'snapshot', blend_file: '/media/scene.blend',
  created_at: '2025-06-01T00:00:00Z', project: PROJECT.id, project_details: PROJECT,
};

function makeJob(
  engine: string, device: string, version: EffectiveBlenderVersion | null,
): Job {
  return {
    id: 42, name: 'Shot 010', asset: ASSET,
    output_file_pattern: 'frame_####.png', start_frame: 1, end_frame: 1,
    status: 'DONE', is_paused: false, status_display: 'Done',
    assigned_worker: null, assigned_worker_hostname: null,
    animation: null, tiled_job: null, animation_frame: null,
    submitted_at: '2025-06-02T00:00:00Z', started_at: null, completed_at: null,
    blender_version: version ? 1 : null, effective_blender_version: version,
    render_engine: engine, render_device: device, cycles_feature_set: 'EXPERIMENTAL',
    render_settings: {}, last_output: '', error_message: '', render_time_seconds: null,
    output_file: null, thumbnail: null,
  };
}

// The display rule reads `series`, so the 4.x and 5.x fixtures differ there.
const V4: EffectiveBlenderVersion = { series: '4.2', resolved_version: '4.2.1' };
const V5: EffectiveBlenderVersion = { series: '5.0', resolved_version: '5.0.1' };

function text(el: Element | null | undefined): string {
  return (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

describe('JobDetailComponent', () => {
  let fixture: ComponentFixture<JobDetailComponent>;
  let jobService: jasmine.SpyObj<JobService>;

  beforeEach(async () => {
    jobService = jasmine.createSpyObj<JobService>('JobService', ['pollDetail']);

    await TestBed.configureTestingModule({
      imports: [JobDetailComponent, NoopAnimationsModule],
      providers: [
        { provide: JobService, useValue: jobService },
        {
          provide: ActivatedRoute,
          useValue: { paramMap: new BehaviorSubject(convertToParamMap({ id: '42' })) },
        },
      ],
    }).compileComponents();
  });

  function render(job: Job): string {
    jobService.pollDetail.and.returnValue(of(job));
    fixture = TestBed.createComponent(JobDetailComponent);
    fixture.detectChanges();
    return text(fixture.nativeElement);
  }

  it('should request the job id from the route', () => {
    render(makeJob('CYCLES', 'GPU', V4));
    expect(jobService.pollDetail).toHaveBeenCalledOnceWith(42, jasmine.any(Function));
  });

  describe('Cycles Feature Set row', () => {
    const cases: { label: string; job: Job; shows: boolean }[] = [
      { label: 'a Cycles job on Blender 4.x', job: makeJob('CYCLES', 'GPU', V4), shows: true },
      { label: 'a Cycles job on Blender 5.x', job: makeJob('CYCLES', 'GPU', V5), shows: false },
      {
        label: 'an Eevee job on Blender 4.x',
        job: makeJob('BLENDER_EEVEE_NEXT', 'CPU', V4), shows: false,
      },
      { label: 'a Workbench job on Blender 4.x', job: makeJob('WORKBENCH', 'ANY', V4), shows: false },
      // Unreal jobs keep the model's default engine (CYCLES) but have no Blender version.
      { label: 'an Unreal job', job: makeJob('CYCLES', 'GPU', null), shows: false },
    ];

    for (const c of cases) {
      it(`should ${c.shows ? 'show' : 'hide'} the feature set for ${c.label}`, () => {
        const page = render(c.job);
        if (c.shows) {
          expect(page).toContain('Cycles Feature Set: EXPERIMENTAL');
        } else {
          expect(page).not.toContain('Cycles Feature Set');
          expect(page).not.toContain('EXPERIMENTAL');
        }
      });

      it(`should still render the other detail rows for ${c.label}`, () => {
        const page = render(c.job);
        expect(page).toContain('Job #42 — Shot 010');
        expect(page).toContain('Status: Done');
        expect(page).toContain(`Render Engine: ${c.job.render_engine}`);
        expect(page).toContain(`Render Device: ${c.job.render_device}`);
        expect(page).toContain('Frames: 1 - 1');
        expect(page).toContain('Worker: Unassigned');
      });
    }

    it('should hide the row when a polled update moves the job to Blender 5.x', () => {
      const updates = new Subject<Job>();
      jobService.pollDetail.and.returnValue(updates);
      fixture = TestBed.createComponent(JobDetailComponent);
      fixture.detectChanges();

      updates.next(makeJob('CYCLES', 'GPU', V4));
      fixture.detectChanges();
      expect(text(fixture.nativeElement)).toContain('Cycles Feature Set');

      updates.next(makeJob('CYCLES', 'GPU', V5));
      fixture.detectChanges();
      expect(text(fixture.nativeElement)).not.toContain('Cycles Feature Set');
    });
  });

  describe('loading and error states', () => {
    it('should show a spinner until the first job arrives', () => {
      jobService.pollDetail.and.returnValue(new Subject<Job>());
      fixture = TestBed.createComponent(JobDetailComponent);
      fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('mat-spinner')).not.toBeNull();
    });

    it('should show "Job not found." when loading fails', () => {
      jobService.pollDetail.and.returnValue(throwError(() => new Error('404')));
      fixture = TestBed.createComponent(JobDetailComponent);
      fixture.detectChanges();
      expect(text(fixture.nativeElement)).toContain('Job not found.');
    });

    it('should unsubscribe from polling on destroy', () => {
      const updates = new Subject<Job>();
      jobService.pollDetail.and.returnValue(updates);
      fixture = TestBed.createComponent(JobDetailComponent);
      fixture.detectChanges();
      expect(updates.observed).toBeTrue();
      fixture.destroy();
      expect(updates.observed).toBeFalse();
    });
  });
});
