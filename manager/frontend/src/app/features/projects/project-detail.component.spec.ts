// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { ActivatedRoute, Router, convertToParamMap } from '@angular/router';
import { MatSnackBar } from '@angular/material/snack-bar';
import { of, throwError, Subject, BehaviorSubject } from 'rxjs';
import { ProjectDetailComponent } from './project-detail.component';
import { ProjectService, Project } from '../../core/services/project.service';
import { AssetService, Asset } from '../../core/services/asset.service';
import { JobService } from '../../core/services/job.service';
import { TiledJobService } from '../../core/services/tiled-job.service';
import { AnimationService } from '../../core/services/animation.service';

const MOCK_VERSION = {
  id: 1, major: 4, minor: 2, series: '4.2',
  resolved_version: '4.2.1', is_default: true,
  added_at: '2025-01-01T00:00:00Z', last_patch_check: null,
};

const MOCK_PROJECT: Project = {
  id: 'abc-uuid-123', name: 'Detail Project', blender_version: 1,
  blender_version_details: MOCK_VERSION,
  created_at: '2025-06-01T00:00:00Z', is_paused: false,
};

const MOCK_ASSET: Asset = {
  id: 1, name: 'scene.blend', blend_file: '/media/scene.blend',
  created_at: '2025-06-01T00:00:00Z', project: 'abc-uuid-123',
  project_details: MOCK_PROJECT,
};

describe('ProjectDetailComponent', () => {
  let component: ProjectDetailComponent;
  let fixture: ComponentFixture<ProjectDetailComponent>;
  let mockProjectService: jasmine.SpyObj<ProjectService>;
  let mockAssetService: jasmine.SpyObj<AssetService>;
  let mockRouter: jasmine.SpyObj<Router>;
  let snackBar: MatSnackBar;
  let paramMapSubject: BehaviorSubject<any>;

  beforeEach(async () => {
    mockProjectService = jasmine.createSpyObj('ProjectService',
      ['get', 'delete', 'pause', 'unpause']);
    mockAssetService = jasmine.createSpyObj('AssetService', ['list']);
    mockRouter = jasmine.createSpyObj('Router', ['navigate']);

    mockProjectService.get.and.returnValue(of(MOCK_PROJECT));
    mockAssetService.list.and.returnValue(of([MOCK_ASSET]));

    paramMapSubject = new BehaviorSubject(convertToParamMap({ id: 'abc-uuid-123' }));

    const mockJobService = jasmine.createSpyObj('JobService', ['pollList']);
    const mockTiledJobService = jasmine.createSpyObj('TiledJobService', ['pollList']);
    const mockAnimationService = jasmine.createSpyObj('AnimationService', ['pollList']);
    mockJobService.pollList.and.returnValue(of([]));
    mockTiledJobService.pollList.and.returnValue(of([]));
    mockAnimationService.pollList.and.returnValue(of([]));

    await TestBed.configureTestingModule({
      imports: [ProjectDetailComponent, NoopAnimationsModule],
      providers: [
        { provide: ProjectService, useValue: mockProjectService },
        { provide: AssetService, useValue: mockAssetService },
        { provide: Router, useValue: mockRouter },
        { provide: ActivatedRoute, useValue: { paramMap: paramMapSubject } },
        { provide: JobService, useValue: mockJobService },
        { provide: TiledJobService, useValue: mockTiledJobService },
        { provide: AnimationService, useValue: mockAnimationService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(ProjectDetailComponent);
    component = fixture.componentInstance;
    snackBar = fixture.debugElement.injector.get(MatSnackBar);
    spyOn(snackBar, 'open');
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should start with loading true', () => {
    expect(component.loading).toBeTrue();
  });

  it('should read route param as string (not number)', () => {
    fixture.detectChanges();
    expect(mockProjectService.get).toHaveBeenCalledWith('abc-uuid-123');
  });

  it('should load project and asset on init', () => {
    fixture.detectChanges();
    expect(component.project).toEqual(MOCK_PROJECT);
    expect(component.asset).toEqual(MOCK_ASSET);
    expect(component.loading).toBeFalse();
  });

  it('should set asset to null when no assets exist', () => {
    mockAssetService.list.and.returnValue(of([]));
    fixture.detectChanges();
    expect(component.asset).toBeNull();
  });

  describe('togglePause', () => {
    beforeEach(() => fixture.detectChanges());

    it('should call pause when project is active', () => {
      mockProjectService.pause.and.returnValue(
        of({ ...MOCK_PROJECT, is_paused: true }));
      component.togglePause();
      expect(mockProjectService.pause).toHaveBeenCalledWith('abc-uuid-123');
    });

    it('should call unpause when project is paused', () => {
      component.project = { ...MOCK_PROJECT, is_paused: true };
      mockProjectService.unpause.and.returnValue(of(MOCK_PROJECT));
      component.togglePause();
      expect(mockProjectService.unpause).toHaveBeenCalledWith('abc-uuid-123');
    });

    it('should update project on success', () => {
      const paused = { ...MOCK_PROJECT, is_paused: true };
      mockProjectService.pause.and.returnValue(of(paused));
      component.togglePause();
      expect(component.project).toEqual(paused);
    });

    it('should show snackbar on error', () => {
      mockProjectService.pause.and.returnValue(throwError(() => new Error()));
      component.togglePause();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Failed to update project', 'Dismiss', { duration: 5000 });
    });
  });

  describe('deleteProject', () => {
    beforeEach(() => fixture.detectChanges());

    it('should navigate to /projects on success', () => {
      mockProjectService.delete.and.returnValue(of(undefined as any));
      component.deleteProject();
      expect(mockRouter.navigate).toHaveBeenCalledWith(['/projects']);
    });

    it('should show success snackbar', () => {
      mockProjectService.delete.and.returnValue(of(undefined as any));
      component.deleteProject();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Project deleted', 'Dismiss', { duration: 3000 });
    });

    it('should show error snackbar and reset deleting on failure', () => {
      mockProjectService.delete.and.returnValue(throwError(() => new Error()));
      component.deleteProject();
      expect(component.deleting).toBeFalse();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Failed to delete project', 'Dismiss', { duration: 5000 });
    });
  });

  it('should unsubscribe on destroy', () => {
    fixture.detectChanges();
    component.ngOnDestroy();
    // No error thrown = subscriptions cleaned up
  });
});
