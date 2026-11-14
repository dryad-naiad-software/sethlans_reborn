// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { SimpleChange } from '@angular/core';
import { of } from 'rxjs';
import { ProjectJobsTableComponent } from './project-jobs-table.component';
import { childErrorMessage, groupFailedChildren } from './project-jobs-table.util';
import { JobService, Job } from '../../core/services/job.service';
import { TiledJobService, TiledJob } from '../../core/services/tiled-job.service';
import { AnimationService, Animation } from '../../core/services/animation.service';
import { Asset } from '../../core/services/asset.service';
import { Project } from '../../core/services/project.service';

// Issue #218: failed jobs must show their error_message in the routed
// project detail jobs table, for single, tiled and animation rows alike.

const BLENDER = { series: '4.2', resolved_version: '4.2.1' };
const RENDER = {
  blender_version: null, effective_blender_version: BLENDER, render_engine: 'CYCLES',
  render_device: 'ANY', cycles_feature_set: 'SUPPORTED', render_settings: {},
};

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    ...RENDER, id: 1, name: 'Single Job', asset: null as unknown as Asset,
    output_file_pattern: '//render/####.png', start_frame: 1, end_frame: 1,
    status: 'QUEUED', is_paused: false, status_display: 'Queued',
    assigned_worker: null, assigned_worker_hostname: null,
    animation: null, tiled_job: null, animation_frame: null,
    submitted_at: '2025-06-03T00:00:00Z', started_at: null, completed_at: null,
    last_output: '', error_message: '', render_time_seconds: null,
    output_file: null, thumbnail: null, ...overrides,
  };
}

function makeTiledJob(overrides: Partial<TiledJob> = {}): TiledJob {
  return {
    ...RENDER, id: 'tiled-uuid', name: 'Tiled Job', status: 'RENDERING', progress: '3/4',
    total_tiles: 4, completed_tiles: 3, project: 'proj-uuid',
    project_details: null as unknown as Project, asset: null as unknown as Asset,
    final_resolution_x: 1920, final_resolution_y: 1080, tile_count_x: 2, tile_count_y: 2,
    submitted_at: '2025-06-02T00:00:00Z', completed_at: null,
    total_render_time_seconds: 0, output_file: null, thumbnail: null, ...overrides,
  };
}

function makeAnimation(overrides: Partial<Animation> = {}): Animation {
  return {
    ...RENDER, id: 7, name: 'Walk Cycle', status: 'DONE', progress: '3/3',
    total_frames: 3, completed_frames: 3, project: 'proj-uuid',
    project_details: null as unknown as Project, asset: null as unknown as Asset,
    output_file_pattern: '//render/walk_####.png', start_frame: 1, end_frame: 3, frame_step: 1,
    tiling_config: 'NONE', submitted_at: '2025-06-01T00:00:00Z', completed_at: null,
    total_render_time_seconds: 0, thumbnail: null, frames: [],
    video_settings: null, video_status: null, video_file: null, video_error: null,
    ...overrides,
  };
}

describe('ProjectJobsTableComponent error messages (#218)', () => {
  let fixture: ComponentFixture<ProjectJobsTableComponent>;
  let component: ProjectJobsTableComponent;
  let jobs: jasmine.SpyObj<JobService>;
  let tiled: jasmine.SpyObj<TiledJobService>;
  let anims: jasmine.SpyObj<AnimationService>;

  function load(data: { jobs?: Job[]; tiled?: TiledJob[]; anims?: Animation[] }): void {
    jobs.list.and.returnValue(of(data.jobs ?? []));
    tiled.list.and.returnValue(of(data.tiled ?? []));
    anims.list.and.returnValue(of(data.anims ?? []));
    component.projectId = 'proj-uuid';
    component.ngOnChanges({ projectId: new SimpleChange('', 'proj-uuid', true) });
    fixture.detectChanges();
  }

  function statusCells(): HTMLElement[] {
    return Array.from(fixture.nativeElement.querySelectorAll('td.mat-column-status'));
  }

  function errorText(cell: HTMLElement): string | undefined {
    return cell.querySelector('.job-error-text')?.textContent?.trim();
  }

  beforeEach(async () => {
    jobs = jasmine.createSpyObj('JobService', ['list']);
    tiled = jasmine.createSpyObj('TiledJobService', ['list']);
    anims = jasmine.createSpyObj('AnimationService', ['list']);
    await TestBed.configureTestingModule({
      imports: [ProjectJobsTableComponent, NoopAnimationsModule],
      providers: [
        { provide: JobService, useValue: jobs },
        { provide: TiledJobService, useValue: tiled },
        { provide: AnimationService, useValue: anims },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(ProjectJobsTableComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => component.ngOnDestroy());

  it('renders the message of an ERROR single job in its status cell', () => {
    load({ jobs: [makeJob({ status: 'ERROR', error_message: 'CUDA out of memory' })] });
    expect(errorText(statusCells()[0])).toBe('CUDA out of memory');
    expect(component.rows[0].errorMessage).toBe('CUDA out of memory');
  });

  for (const blank of ['', '   ']) {
    it(`renders no error element for an ERROR job with message ${JSON.stringify(blank)}`, () => {
      load({ jobs: [makeJob({ status: 'ERROR', error_message: blank })] });
      const cell = statusCells()[0];
      expect(cell.querySelector('.job-error')).toBeNull();
      expect(cell.textContent!.trim()).toBe('error ERROR');
      expect(component.rows[0].errorMessage).toBeNull();
    });
  }

  it('renders no error element for an ERROR job whose message is null', () => {
    const job = makeJob({ status: 'ERROR' });
    (job as { error_message: string | null }).error_message = null;
    load({ jobs: [job] });
    expect(statusCells()[0].querySelector('.job-error')).toBeNull();
  });

  it('leaves non-ERROR jobs unchanged, even with a stale message', () => {
    load({
      jobs: [
        makeJob({ id: 1, status: 'DONE', error_message: 'old failure before requeue' }),
        makeJob({ id: 2, status: 'QUEUED' }),
      ],
    });
    const cells = statusCells();
    expect(cells.length).toBe(2);
    for (const cell of cells) expect(cell.querySelector('.job-error')).toBeNull();
    expect(cells.map(c => c.textContent!.trim()).sort())
      .toEqual(['check_circle DONE', 'hourglass_empty QUEUED']);
    expect(component.rows.every(r => r.errorMessage === null)).toBeTrue();
  });

  it('shows a failed tile message on its tiled job row', () => {
    load({
      jobs: [makeJob({
        id: 5, name: 'Tiled Job_Tile_0_1', tiled_job: 'tiled-uuid',
        status: 'ERROR', error_message: 'Segfault in Cycles',
      })],
      tiled: [makeTiledJob()],
    });
    expect(component.rows.length).toBe(1);
    expect(errorText(statusCells()[0])).toBe('1 tile failed. Tiled Job_Tile_0_1: Segfault in Cycles');
  });

  it('shows failed frame messages on their animation row', () => {
    load({
      jobs: [
        makeJob({ id: 8, name: 'Walk_Frame_0002', animation: 7, start_frame: 2, status: 'ERROR', error_message: '' }),
        makeJob({ id: 9, name: 'Walk_Frame_0003', animation: 7, start_frame: 3, status: 'ERROR', error_message: 'Missing texture' }),
        makeJob({ id: 10, name: 'Walk_Frame_0001', animation: 7, start_frame: 1, status: 'DONE' }),
      ],
      anims: [makeAnimation()],
    });
    expect(errorText(statusCells()[0])).toBe('2 frames failed. Walk_Frame_0003: Missing texture');
  });

  it('still shows a count when every failed child has a blank message', () => {
    load({
      jobs: [
        makeJob({ id: 5, name: 'Tiled Job_Tile_0_0', tiled_job: 'tiled-uuid', status: 'ERROR', error_message: '' }),
        makeJob({ id: 6, name: 'Tiled Job_Tile_0_1', tiled_job: 'tiled-uuid', status: 'ERROR', error_message: '' }),
      ],
      tiled: [makeTiledJob()],
    });
    expect(component.rows[0].status).toBe('RENDERING');
    expect(errorText(statusCells()[0])).toBe('2 tiles failed.');
  });

  it('counts failed tiles of a tiled animation as failed frames', () => {
    const tile = (id: number, frame: number, msg: string) => makeJob({
      id, name: `Walk_Frame_000${frame}_Tile_0_${id}`, animation: 7, animation_frame: 100 + frame,
      start_frame: frame, end_frame: frame, status: 'ERROR', error_message: msg,
    });
    load({
      jobs: [tile(1, 2, ''), tile(2, 2, 'OOM'), tile(3, 3, '')],
      anims: [makeAnimation({ tiling_config: '2x2', status: 'RENDERING' })],
    });
    expect(errorText(statusCells()[0])).toBe('2 frames failed. Walk_Frame_0002_Tile_0_2: OOM');
  });

  it('shows nothing on tiled or animation rows with no failed children', () => {
    load({
      jobs: [makeJob({ id: 5, tiled_job: 'tiled-uuid', status: 'DONE', error_message: 'stale' })],
      tiled: [makeTiledJob({ status: 'DONE' })],
      anims: [makeAnimation()],
    });
    for (const cell of statusCells()) expect(cell.querySelector('.job-error')).toBeNull();
  });

  it('keeps an expanded message expanded across a polling refresh', () => {
    load({ jobs: [makeJob({ status: 'ERROR', error_message: 'boom' })] });
    (statusCells()[0].querySelector('.job-error-toggle') as HTMLButtonElement).click();
    fixture.detectChanges();
    component.triggerRefresh();
    fixture.detectChanges();
    expect(statusCells()[0].querySelector('.job-error-text')!.classList).toContain('expanded');
  });

  it('reuses the same row node when a polled row changes and updates its cells', () => {
    load({ tiled: [makeTiledJob({ status: 'RENDERING', completed_tiles: 1, progress: '1/4' })] });
    const rowBefore = fixture.nativeElement.querySelector('tr.mat-mdc-row');
    expect(rowBefore).toBeTruthy();
    expect(errorText(statusCells()[0])).toBeUndefined();

    jobs.list.and.returnValue(of([makeJob({
      id: 5, name: 'Tiled Job_Tile_1_1', tiled_job: 'tiled-uuid',
      status: 'ERROR', error_message: 'GPU lost',
    })]));
    tiled.list.and.returnValue(of([makeTiledJob({ status: 'ERROR', completed_tiles: 3, progress: '3/4' })]));
    component.triggerRefresh();
    fixture.detectChanges();

    const rows = fixture.nativeElement.querySelectorAll('tr.mat-mdc-row');
    expect(rows.length).toBe(1);
    expect(rows[0]).toBe(rowBefore);
    const cell = statusCells()[0];
    expect(cell.textContent).toContain('ERROR');
    expect(cell.classList).toContain('status-ERROR');
    expect(rows[0].querySelector('.progress-text').textContent.trim()).toBe('3 / 4 tiles');
    expect(errorText(cell)).toBe('1 tile failed. Tiled Job_Tile_1_1: GPU lost');
  });

  it('childErrorMessage ignores children that are not in ERROR', () => {
    expect(childErrorMessage([makeJob({ status: 'CANCELED', error_message: 'x' })], 'tile')).toBeNull();
    expect(childErrorMessage([], 'frame')).toBeNull();
  });

  it('groupFailedChildren keeps only ERROR children, keyed by parent', () => {
    const groups = groupFailedChildren([
      makeJob({ id: 1, status: 'ERROR' }),
      makeJob({ id: 2, tiled_job: 't1', status: 'ERROR' }),
      makeJob({ id: 3, tiled_job: 't1', status: 'DONE' }),
      makeJob({ id: 4, animation: 7, status: 'ERROR' }),
      makeJob({ id: 5, animation: 8, status: 'RENDERING' }),
    ]);
    expect(groups.byTiledJob.get('t1')!.map(j => j.id)).toEqual([2]);
    expect(groups.byAnimation.get(7)!.map(j => j.id)).toEqual([4]);
    expect(groups.byAnimation.has(8)).toBeFalse();
  });
});
