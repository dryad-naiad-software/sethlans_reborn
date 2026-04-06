// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { Router } from '@angular/router';
import { MatDialog } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { of, throwError, Subject } from 'rxjs';
import { ProjectListComponent } from './project-list.component';
import { ProjectService, Project } from '../../core/services/project.service';

const MOCK_VERSION = {
  id: 1, major: 4, minor: 2, series: '4.2',
  resolved_version: '4.2.1', is_default: true,
  added_at: '2025-01-01T00:00:00Z', last_patch_check: null,
};

const MOCK_PROJECTS: Project[] = [
  { id: 'uuid-1', name: 'Project A', blender_version: 1,
    blender_version_details: MOCK_VERSION,
    created_at: '2025-06-01T00:00:00Z' },
  { id: 'uuid-2', name: 'Project B', blender_version: 1,
    blender_version_details: MOCK_VERSION,
    created_at: '2025-06-02T00:00:00Z' },
];

describe('ProjectListComponent', () => {
  let component: ProjectListComponent;
  let fixture: ComponentFixture<ProjectListComponent>;
  let mockProjectService: jasmine.SpyObj<ProjectService>;
  let mockRouter: jasmine.SpyObj<Router>;
  let dialog: MatDialog;
  let snackBar: MatSnackBar;

  beforeEach(async () => {
    mockProjectService = jasmine.createSpyObj('ProjectService',
      ['pollList', 'delete']);
    mockRouter = jasmine.createSpyObj('Router', ['navigate']);

    mockProjectService.pollList.and.returnValue(of(MOCK_PROJECTS));

    await TestBed.configureTestingModule({
      imports: [ProjectListComponent, NoopAnimationsModule],
      providers: [
        { provide: ProjectService, useValue: mockProjectService },
        { provide: Router, useValue: mockRouter },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(ProjectListComponent);
    component = fixture.componentInstance;
    snackBar = fixture.debugElement.injector.get(MatSnackBar);
    dialog = fixture.debugElement.injector.get(MatDialog);
    spyOn(snackBar, 'open');
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should show loading spinner initially', () => {
    expect(component.loading).toBeTrue();
  });

  it('should load projects on init and set loading to false', () => {
    fixture.detectChanges();
    expect(component.loading).toBeFalse();
    expect(component.projects.length).toBe(2);
  });

  it('should display correct columns', () => {
    expect(component.displayedColumns).toEqual(
      ['name', 'blender_version', 'created_at', 'actions']
    );
  });

  describe('empty state', () => {
    beforeEach(() => {
      mockProjectService.pollList.and.returnValue(of([]));
      fixture = TestBed.createComponent(ProjectListComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
    });

    it('should show empty state message when no projects', () => {
      const el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('.empty-state')?.textContent)
        .toContain("No projects yet. Click 'New Project' to get started.");
    });
  });

  describe('error handling', () => {
    beforeEach(() => {
      mockProjectService.pollList.and.returnValue(throwError(() => new Error('fail')));
      fixture = TestBed.createComponent(ProjectListComponent);
      component = fixture.componentInstance;
      snackBar = fixture.debugElement.injector.get(MatSnackBar);
      (snackBar.open as jasmine.Spy).calls.reset();
      fixture.detectChanges();
    });

    it('should show snackbar on load error', () => {
      expect(snackBar.open).toHaveBeenCalledWith(
        'Failed to load projects', 'Dismiss', { duration: 5000 }
      );
    });

    it('should set loading to false on error', () => {
      expect(component.loading).toBeFalse();
    });
  });

  describe('confirmDelete', () => {
    it('should open confirm dialog and delete on confirm', () => {
      const afterClosed$ = new Subject<boolean>();
      spyOn(dialog, 'open').and.returnValue({ afterClosed: () => afterClosed$ } as any);
      mockProjectService.delete.and.returnValue(of(undefined as any));
      component.confirmDelete(MOCK_PROJECTS[0]);
      expect(dialog.open).toHaveBeenCalled();
      afterClosed$.next(true);
      expect(mockProjectService.delete).toHaveBeenCalledWith('uuid-1');
      expect(snackBar.open).toHaveBeenCalledWith(
        'Project deleted', 'Dismiss', { duration: 3000 }
      );
    });

    it('should not delete when dialog is dismissed', () => {
      const afterClosed$ = new Subject<boolean>();
      spyOn(dialog, 'open').and.returnValue({ afterClosed: () => afterClosed$ } as any);
      component.confirmDelete(MOCK_PROJECTS[0]);
      afterClosed$.next(false);
      afterClosed$.complete();
      expect(mockProjectService.delete).not.toHaveBeenCalled();
    });
  });

  describe('openCreateDialog', () => {
    it('should open the dialog and navigate on result', () => {
      const afterClosed$ = new Subject<Project | undefined>();
      spyOn(dialog, 'open').and.returnValue({ afterClosed: () => afterClosed$ } as any);
      component.openCreateDialog();
      expect(dialog.open).toHaveBeenCalled();

      afterClosed$.next(MOCK_PROJECTS[0]);
      expect(mockRouter.navigate).toHaveBeenCalledWith(['/projects', 'uuid-1']);
    });

    it('should not navigate when dialog is cancelled', () => {
      const afterClosed$ = new Subject<Project | undefined>();
      spyOn(dialog, 'open').and.returnValue({ afterClosed: () => afterClosed$ } as any);
      component.openCreateDialog();
      afterClosed$.next(undefined);
      expect(mockRouter.navigate).not.toHaveBeenCalled();
    });
  });

  it('should unsubscribe on destroy', () => {
    fixture.detectChanges();
    expect((component as any).sub).toBeTruthy();
    component.ngOnDestroy();
    // No error thrown = subscription cleaned up
  });
});
