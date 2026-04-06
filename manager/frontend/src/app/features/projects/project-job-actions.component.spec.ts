// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, fakeAsync, TestBed, tick } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatDialogModule } from '@angular/material/dialog';
import { of } from 'rxjs';
import { ProjectJobActionsComponent } from './project-job-actions.component';
import { JobService } from '../../core/services/job.service';
import { TiledJobService } from '../../core/services/tiled-job.service';
import { AnimationService } from '../../core/services/animation.service';
import { JobTableRow } from './project-jobs-table.util';

function makeRow(overrides: Partial<JobTableRow> = {}): JobTableRow {
  return {
    id: 1, name: 'Test Job', type: 'single',
    status: 'QUEUED', is_paused: false,
    worker: '--', time: '--',
    createdAt: '2025-06-01T00:00:00Z',
    thumbnail: null, outputFile: null,
    ...overrides,
  };
}

describe('ProjectJobActionsComponent', () => {
  let component: ProjectJobActionsComponent;
  let fixture: ComponentFixture<ProjectJobActionsComponent>;
  let mockJobService: jasmine.SpyObj<JobService>;
  let mockTiledJobService: jasmine.SpyObj<TiledJobService>;
  let mockAnimationService: jasmine.SpyObj<AnimationService>;
  let dialogOpenSpy: jasmine.Spy;

  beforeEach(async () => {
    mockJobService = jasmine.createSpyObj('JobService', [
      'pause', 'unpause', 'cancel', 'requeue', 'delete',
    ]);
    mockTiledJobService = jasmine.createSpyObj('TiledJobService', [
      'pause', 'unpause', 'delete',
    ]);
    mockAnimationService = jasmine.createSpyObj('AnimationService', [
      'pause', 'unpause', 'delete',
    ]);

    await TestBed.configureTestingModule({
      imports: [ProjectJobActionsComponent, NoopAnimationsModule, MatDialogModule],
      providers: [
        { provide: JobService, useValue: mockJobService },
        { provide: TiledJobService, useValue: mockTiledJobService },
        { provide: AnimationService, useValue: mockAnimationService },
      ],
    }).compileComponents();
  });

  function createComponent(row: JobTableRow): void {
    fixture = TestBed.createComponent(ProjectJobActionsComponent);
    component = fixture.componentInstance;
    component.row = row;
    // Spy on the component's own dialog instance
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const compDialog = (component as any).dialog;
    dialogOpenSpy = spyOn(compDialog, 'open').and.returnValue({
      afterClosed: () => of(true),
    });
    fixture.detectChanges();
  }

  function findDeleteButton(): HTMLButtonElement | null {
    const buttons: NodeListOf<HTMLButtonElement> =
      fixture.nativeElement.querySelectorAll('button');
    for (const btn of Array.from(buttons)) {
      if (btn.getAttribute('aria-label') === 'Delete job') return btn;
    }
    return null;
  }

  it('should create', () => {
    createComponent(makeRow());
    expect(component).toBeTruthy();
  });

  describe('delete button visibility', () => {
    it('should show delete button for single jobs', () => {
      createComponent(makeRow({ type: 'single' }));
      expect(findDeleteButton()).toBeTruthy();
    });

    it('should show delete button for tiled jobs', () => {
      createComponent(makeRow({ type: 'tiled', id: 'tiled-uuid' }));
      expect(findDeleteButton()).toBeTruthy();
    });

    it('should show delete button for animation jobs', () => {
      createComponent(makeRow({ type: 'animation', id: 2 }));
      expect(findDeleteButton()).toBeTruthy();
    });
  });

  describe('delete dispatches to correct service', () => {
    it('should call jobService.delete for single jobs', fakeAsync(() => {
      mockJobService.delete.and.returnValue(of(void 0));
      createComponent(makeRow({ type: 'single', id: 10 }));

      component.onDelete();
      tick();

      expect(dialogOpenSpy).toHaveBeenCalled();
      expect(mockJobService.delete).toHaveBeenCalledWith(10);
    }));

    it('should call tiledJobService.delete for tiled jobs', fakeAsync(() => {
      mockTiledJobService.delete.and.returnValue(of(void 0));
      createComponent(makeRow({ type: 'tiled', id: 'tiled-uuid-123' }));

      component.onDelete();
      tick();

      expect(dialogOpenSpy).toHaveBeenCalled();
      expect(mockTiledJobService.delete).toHaveBeenCalledWith('tiled-uuid-123');
    }));

    it('should call animationService.delete for animation jobs', fakeAsync(() => {
      mockAnimationService.delete.and.returnValue(of(void 0));
      createComponent(makeRow({ type: 'animation', id: 5 }));

      component.onDelete();
      tick();

      expect(dialogOpenSpy).toHaveBeenCalled();
      expect(mockAnimationService.delete).toHaveBeenCalledWith(5);
    }));

    it('should emit deleted event on successful delete', fakeAsync(() => {
      mockTiledJobService.delete.and.returnValue(of(void 0));
      createComponent(makeRow({ type: 'tiled', id: 'tiled-uuid' }));
      spyOn(component.deleted, 'emit');

      component.onDelete();
      tick();

      expect(component.deleted.emit).toHaveBeenCalled();
    }));

    it('should not call delete when dialog is dismissed', fakeAsync(() => {
      createComponent(makeRow({ type: 'single', id: 1 }));
      dialogOpenSpy.and.returnValue({
        afterClosed: () => of(false),
      });

      component.onDelete();
      tick();

      expect(mockJobService.delete).not.toHaveBeenCalled();
    }));
  });
});
