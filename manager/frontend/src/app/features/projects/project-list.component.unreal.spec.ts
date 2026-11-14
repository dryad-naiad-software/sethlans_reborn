// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { ProjectListComponent } from './project-list.component';
import { ProjectService, Project } from '../../core/services/project.service';

/**
 * Unreal foundation spec FR-FE-1 / FR-FE-3 / FR-FE-5 (AC-25, AC-39).
 *
 * An Unreal project has `blender_version_details: null`; before the guard
 * the list template threw a TypeError for every user. The fixtures mirror
 * the real API shape, which FR-T-12's contract test checks against a real
 * database (`tests/integration/frontend/test_project_renderer_contract.py`).
 */

const BLENDER_PROJECT: Project = {
  id: 'blender-uuid', name: 'Blender Project', renderer: 'BLENDER',
  blender_version: 1,
  blender_version_details: {
    id: 1, major: 4, minor: 2, series: '4.2', resolved_version: '4.2.1',
    is_default: true, added_at: '2025-01-01T00:00:00Z', last_patch_check: null,
  },
  engine_build: '', created_at: '2025-06-01T00:00:00Z',
};

const UNREAL_PROJECT: Project = {
  id: 'unreal-uuid', name: 'Unreal Project', renderer: 'UNREAL',
  blender_version: null, blender_version_details: null,
  engine_build: '5.8.4-58902052', created_at: '2025-06-02T00:00:00Z',
};

/** Collapse template whitespace so `4.2\n  (4.2.1)` compares as `4.2 (4.2.1)`. */
function text(el: Element | null | undefined): string {
  return (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

describe('ProjectListComponent with Unreal projects', () => {
  let fixture: ComponentFixture<ProjectListComponent>;
  let consoleError: jasmine.Spy;

  beforeEach(async () => {
    const projectService = jasmine.createSpyObj<ProjectService>('ProjectService', ['pollList', 'delete']);
    projectService.pollList.and.returnValue(of([BLENDER_PROJECT, UNREAL_PROJECT]));

    await TestBed.configureTestingModule({
      imports: [ProjectListComponent, NoopAnimationsModule],
      providers: [
        { provide: ProjectService, useValue: projectService },
        provideRouter([]),
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(ProjectListComponent);
    consoleError = spyOn(console, 'error');
  });

  function render(): void {
    fixture.detectChanges();
  }

  function versionCells(): string[] {
    const cells = fixture.nativeElement.querySelectorAll('td.mat-column-blender_version');
    return Array.from(cells, (c: Element) => text(c));
  }

  it('renders a mixed Blender/Unreal list without throwing or logging an error', () => {
    expect(() => render()).not.toThrow();
    expect(consoleError).not.toHaveBeenCalled();
    expect(versionCells().length).toBe(2);
  });

  it('shows the Unreal build and the unchanged Blender version text', () => {
    render();
    expect(versionCells()).toEqual(['4.2 (4.2.1)', 'Unreal 5.8.4-58902052']);
    expect(consoleError).not.toHaveBeenCalled();
  });

  it('heads the version column "Renderer" and keeps its column id', () => {
    render();
    const header = fixture.nativeElement.querySelector('th.mat-column-blender_version');
    expect(text(header)).toBe('Renderer');
    expect(fixture.componentInstance.displayedColumns).toContain('blender_version');
  });

  it('still renders both project names', () => {
    render();
    const names = Array.from(
      fixture.nativeElement.querySelectorAll('td.mat-column-name'), (c: Element) => text(c));
    expect(names).toEqual(['Blender Project', 'Unreal Project']);
  });
});
