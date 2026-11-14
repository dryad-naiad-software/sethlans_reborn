// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { ActivatedRoute, Router, convertToParamMap } from '@angular/router';
import { MatDialog } from '@angular/material/dialog';
import { By } from '@angular/platform-browser';
import { BehaviorSubject, of } from 'rxjs';
import { ProjectDetailComponent } from './project-detail.component';
import { ProjectJobsTableComponent } from './project-jobs-table.component';
import { JobCreateFormComponent } from './job-create-form.component';
import { ProjectService, Project } from '../../core/services/project.service';
import { AssetService, Asset } from '../../core/services/asset.service';
import { JobService, Job } from '../../core/services/job.service';
import { TiledJobService } from '../../core/services/tiled-job.service';
import { AnimationService } from '../../core/services/animation.service';

/**
 * Unreal foundation spec FR-FE-1 / FR-FE-3 / FR-FE-4 (AC-25, AC-39).
 *
 * An Unreal project (null `blender_version_details`) renders without error,
 * shows its engine build, and offers no Blender job form: Create Job is
 * disabled with an inline hint and Re-render does not open the dialog.
 * The Blender cases pin today's behaviour (button enabled iff an asset
 * exists, no hint). Fixtures mirror the API shape checked by FR-T-12.
 */

const HINT = 'Unreal jobs are submitted through the API in this version.';

const UNREAL_PROJECT: Project = {
  id: 'unreal-uuid', name: 'Unreal Project', renderer: 'UNREAL',
  blender_version: null, blender_version_details: null,
  engine_build: '5.8.4-58902052', created_at: '2025-06-01T00:00:00Z',
};

const BLENDER_PROJECT: Project = {
  id: 'blender-uuid', name: 'Blender Project', renderer: 'BLENDER',
  blender_version: 1,
  blender_version_details: {
    id: 1, major: 4, minor: 2, series: '4.2', resolved_version: '4.2.1',
    is_default: true, added_at: '2025-01-01T00:00:00Z', last_patch_check: null,
  },
  engine_build: '', created_at: '2025-06-01T00:00:00Z',
};

function assetFor(project: Project, blendFile: string | null): Asset {
  return {
    id: 5, name: 'snapshot', blend_file: blendFile, created_at: '2025-06-01T00:00:00Z',
    project: project.id, project_details: project,
  };
}

const UNREAL_ASSET = assetFor(UNREAL_PROJECT, null);

const UNREAL_JOB: Job = {
  id: 11, name: 'Unreal Still', asset: UNREAL_ASSET,
  output_file_pattern: 'frame_####.png', start_frame: 1, end_frame: 1,
  status: 'DONE', is_paused: false, status_display: 'Done',
  assigned_worker: null, assigned_worker_hostname: null,
  animation: null, tiled_job: null, animation_frame: null,
  submitted_at: '2025-06-02T00:00:00Z', started_at: null, completed_at: null,
  blender_version: null, effective_blender_version: null,
  render_engine: 'CYCLES', render_device: 'GPU', cycles_feature_set: 'SUPPORTED',
  render_settings: {}, last_output: '', error_message: '', render_time_seconds: null,
  output_file: null, thumbnail: null,
};

function text(el: Element | null | undefined): string {
  return (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

describe('ProjectDetailComponent renderer handling', () => {
  let fixture: ComponentFixture<ProjectDetailComponent>;
  let projectService: jasmine.SpyObj<ProjectService>;
  let assetService: jasmine.SpyObj<AssetService>;
  let jobService: jasmine.SpyObj<JobService>;
  let consoleError: jasmine.Spy;

  beforeEach(async () => {
    projectService = jasmine.createSpyObj<ProjectService>('ProjectService', ['get', 'delete']);
    assetService = jasmine.createSpyObj<AssetService>('AssetService', ['list']);
    jobService = jasmine.createSpyObj<JobService>('JobService', ['pollList', 'list']);
    const tiled = jasmine.createSpyObj<TiledJobService>('TiledJobService', ['pollList', 'list']);
    const anims = jasmine.createSpyObj<AnimationService>('AnimationService', ['pollList', 'list']);
    jobService.pollList.and.returnValue(of([]));
    tiled.pollList.and.returnValue(of([]));
    tiled.list.and.returnValue(of([]));
    anims.pollList.and.returnValue(of([]));
    anims.list.and.returnValue(of([]));

    await TestBed.configureTestingModule({
      imports: [ProjectDetailComponent, NoopAnimationsModule],
      providers: [
        { provide: ProjectService, useValue: projectService },
        { provide: AssetService, useValue: assetService },
        { provide: Router, useValue: jasmine.createSpyObj<Router>('Router', ['navigate']) },
        {
          provide: ActivatedRoute,
          useValue: { paramMap: new BehaviorSubject(convertToParamMap({ id: 'unreal-uuid' })) },
        },
        { provide: JobService, useValue: jobService },
        { provide: TiledJobService, useValue: tiled },
        { provide: AnimationService, useValue: anims },
      ],
    }).compileComponents();
    consoleError = spyOn(console, 'error');
  });

  function render(project: Project, assets: Asset[], jobs: Job[] = []): void {
    projectService.get.and.returnValue(of(project));
    assetService.list.and.returnValue(of(assets));
    jobService.list.and.returnValue(of(jobs));
    fixture = TestBed.createComponent(ProjectDetailComponent);
    expect(() => fixture.detectChanges()).not.toThrow();
  }

  function createJobButton(): HTMLButtonElement | undefined {
    const buttons: HTMLButtonElement[] = Array.from(fixture.nativeElement.querySelectorAll('button'));
    return buttons.find(b => text(b).includes('Create Job'));
  }

  function hint(): Element | null {
    return fixture.nativeElement.querySelector('.hint');
  }

  describe('Unreal project', () => {
    beforeEach(() => render(UNREAL_PROJECT, [UNREAL_ASSET], [UNREAL_JOB]));

    it('renders the engine build without throwing or logging an error', () => {
      expect(text(fixture.nativeElement.querySelector('.subtitle'))).toContain('Unreal 5.8.4-58902052');
      expect(text(fixture.nativeElement.querySelector('.subtitle'))).not.toContain('Blender');
      expect(consoleError).not.toHaveBeenCalled();
    });

    it('renders the Unreal job row in the jobs table', () => {
      const names = Array.from(
        fixture.nativeElement.querySelectorAll('td.mat-column-name'), (c: Element) => text(c));
      expect(names.some(n => n.includes('Unreal Still'))).toBeTrue();
      expect(consoleError).not.toHaveBeenCalled();
    });

    it('disables Create Job and shows the inline hint', () => {
      expect(createJobButton()?.disabled).toBeTrue();
      expect(text(hint())).toBe(HINT);
    });

    it('does not open the Blender form from Create Job or Re-render', () => {
      const dialog = fixture.debugElement.injector.get(MatDialog);
      const open = spyOn(dialog, 'open');
      fixture.componentInstance.openCreateRender();
      const table = fixture.debugElement.query(By.directive(ProjectJobsTableComponent));
      const jobsTable: ProjectJobsTableComponent = table.componentInstance;
      jobsTable.rerender.emit({ renderType: 'single', samples: 64 });
      expect(open).not.toHaveBeenCalled();
    });
  });

  describe('Blender project (unchanged behaviour)', () => {
    it('shows the Blender version, enables Create Job and shows no hint', () => {
      render(BLENDER_PROJECT, [assetFor(BLENDER_PROJECT, '/media/scene.blend')]);
      expect(text(fixture.nativeElement.querySelector('.subtitle'))).toContain('Blender 4.2 (4.2.1)');
      expect(createJobButton()?.disabled).toBeFalse();
      expect(hint()).toBeNull();
    });

    it('disables Create Job without a hint when there is no asset', () => {
      render(BLENDER_PROJECT, []);
      expect(createJobButton()?.disabled).toBeTrue();
      expect(hint()).toBeNull();
    });

    it('opens the job form from Re-render', () => {
      render(BLENDER_PROJECT, [assetFor(BLENDER_PROJECT, '/media/scene.blend')]);
      const dialog = fixture.debugElement.injector.get(MatDialog);
      const open = spyOn(dialog, 'open');
      const table = fixture.debugElement.query(By.directive(ProjectJobsTableComponent));
      const jobsTable: ProjectJobsTableComponent = table.componentInstance;
      const prefill = { renderType: 'single' as const, samples: 64 };
      jobsTable.rerender.emit(prefill);
      expect(open).toHaveBeenCalledWith(JobCreateFormComponent, {
        width: '700px', data: { projectId: 'blender-uuid', assetId: 5, prefill },
      });
    });
  });
});
