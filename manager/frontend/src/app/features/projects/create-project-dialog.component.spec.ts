// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatDialogRef } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { HttpEventType, HttpResponse } from '@angular/common/http';
import { of, throwError } from 'rxjs';
import { CreateProjectDialogComponent } from './create-project-dialog.component';
import { ProjectService, Project } from '../../core/services/project.service';
import { AssetService } from '../../core/services/asset.service';
import { SupportedVersionService, SupportedVersion } from '../../core/services/supported-version.service';

const MOCK_VERSIONS: SupportedVersion[] = [
  { id: 1, major: 4, minor: 1, series: '4.1', resolved_version: '4.1.0',
    is_default: false, added_at: '2025-01-01T00:00:00Z', last_patch_check: null },
  { id: 2, major: 4, minor: 2, series: '4.2', resolved_version: '4.2.1',
    is_default: true, added_at: '2025-01-01T00:00:00Z', last_patch_check: null },
];

const MOCK_PROJECT: Project = {
  id: 'new-proj-uuid', name: 'New Project', blender_version: 2,
  blender_version_details: MOCK_VERSIONS[1],
  created_at: '2025-06-01T00:00:00Z', is_paused: false,
};

describe('CreateProjectDialogComponent', () => {
  let component: CreateProjectDialogComponent;
  let fixture: ComponentFixture<CreateProjectDialogComponent>;
  let mockDialogRef: jasmine.SpyObj<MatDialogRef<CreateProjectDialogComponent>>;
  let mockProjectService: jasmine.SpyObj<ProjectService>;
  let mockAssetService: jasmine.SpyObj<AssetService>;
  let mockVersionService: jasmine.SpyObj<SupportedVersionService>;
  let snackBar: MatSnackBar;

  beforeEach(async () => {
    mockDialogRef = jasmine.createSpyObj('MatDialogRef', ['close']);
    mockProjectService = jasmine.createSpyObj('ProjectService', ['create', 'delete']);
    mockAssetService = jasmine.createSpyObj('AssetService', ['upload']);
    mockVersionService = jasmine.createSpyObj('SupportedVersionService', ['list']);
    mockVersionService.list.and.returnValue(of(MOCK_VERSIONS));

    await TestBed.configureTestingModule({
      imports: [CreateProjectDialogComponent, NoopAnimationsModule],
      providers: [
        { provide: MatDialogRef, useValue: mockDialogRef },
        { provide: ProjectService, useValue: mockProjectService },
        { provide: AssetService, useValue: mockAssetService },
        { provide: SupportedVersionService, useValue: mockVersionService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(CreateProjectDialogComponent);
    component = fixture.componentInstance;
    snackBar = fixture.debugElement.injector.get(MatSnackBar);
    spyOn(snackBar, 'open');
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should load versions on init', () => {
    expect(component.versions).toEqual(MOCK_VERSIONS);
  });

  it('should pre-select default version', () => {
    expect(component.form.controls.blenderVersion.value).toBe(2);
  });

  it('should show snackbar when version loading fails', () => {
    mockVersionService.list.and.returnValue(throwError(() => new Error()));
    const f = TestBed.createComponent(CreateProjectDialogComponent);
    (snackBar.open as jasmine.Spy).calls.reset();
    f.detectChanges();
    expect(snackBar.open).toHaveBeenCalledWith(
      'Failed to load Blender versions', 'Dismiss', { duration: 5000 });
  });

  describe('form validation', () => {
    it('should be invalid initially (name is empty)', () => {
      expect(component.form.invalid).toBeTrue();
    });

    it('should reject name shorter than 4 characters', () => {
      component.form.controls.name.setValue('abc');
      expect(component.form.controls.name.hasError('minlength')).toBeTrue();
    });

    it('should reject name longer than 40 characters', () => {
      component.form.controls.name.setValue('a'.repeat(41));
      expect(component.form.controls.name.hasError('maxlength')).toBeTrue();
    });

    it('should accept valid name', () => {
      component.form.controls.name.setValue('Valid Name');
      expect(component.form.controls.name.valid).toBeTrue();
    });

    it('should require blender version', () => {
      component.form.controls.blenderVersion.setValue(null);
      expect(component.form.controls.blenderVersion.hasError('required')).toBeTrue();
    });
  });

  describe('onFileSelected', () => {
    it('should accept .blend files', () => {
      const file = new File(['data'], 'scene.blend');
      const event = { target: { files: [file] } } as unknown as Event;
      component.onFileSelected(event);
      expect(component.selectedFile).toBe(file);
    });

    it('should reject non-.blend files', () => {
      const file = new File(['data'], 'image.png');
      const event = { target: { files: [file] } } as unknown as Event;
      component.onFileSelected(event);
      expect(component.selectedFile).toBeNull();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Only .blend files are accepted', 'Dismiss', { duration: 5000 });
    });

    it('should do nothing if no file selected', () => {
      const event = { target: { files: [] } } as unknown as Event;
      component.onFileSelected(event);
      expect(component.selectedFile).toBeNull();
    });
  });

  describe('onSubmit', () => {
    beforeEach(() => {
      component.form.controls.name.setValue('New Project');
      component.form.controls.blenderVersion.setValue(2);
      component.selectedFile = new File(['data'], 'scene.blend');
    });

    it('should not submit when form is invalid', () => {
      component.form.controls.name.setValue('');
      component.onSubmit();
      expect(mockProjectService.create).not.toHaveBeenCalled();
    });

    it('should not submit when no file is selected', () => {
      component.selectedFile = null;
      component.onSubmit();
      expect(mockProjectService.create).not.toHaveBeenCalled();
    });

    it('should create project then upload asset on success', () => {
      mockProjectService.create.and.returnValue(of(MOCK_PROJECT));
      const uploadResponse = new HttpResponse({ body: { id: 1 } });
      mockAssetService.upload.and.returnValue(of(uploadResponse as any));

      component.onSubmit();

      expect(mockProjectService.create).toHaveBeenCalledWith({
        name: 'New Project', blender_version: 2,
      });
      expect(mockAssetService.upload).toHaveBeenCalledWith(
        'new-proj-uuid', 'scene.blend', component.selectedFile!);
    });

    it('should close dialog with project on upload success', () => {
      mockProjectService.create.and.returnValue(of(MOCK_PROJECT));
      const uploadResponse = new HttpResponse({ body: { id: 1 } });
      mockAssetService.upload.and.returnValue(of(uploadResponse as any));

      component.onSubmit();
      expect(mockDialogRef.close).toHaveBeenCalledWith(MOCK_PROJECT);
    });

    it('should show error and reset uploading on project creation failure', () => {
      mockProjectService.create.and.returnValue(
        throwError(() => ({ error: { detail: 'Name taken' } })));
      component.onSubmit();
      expect(component.uploading).toBeFalse();
      expect(snackBar.open).toHaveBeenCalledWith(
        'Name taken', 'Dismiss', { duration: 5000 });
    });

    it('should delete orphaned project on upload failure', () => {
      mockProjectService.create.and.returnValue(of(MOCK_PROJECT));
      mockAssetService.upload.and.returnValue(
        throwError(() => ({ error: { detail: 'Upload failed' } })));
      mockProjectService.delete.and.returnValue(of(undefined as any));

      component.onSubmit();

      expect(mockProjectService.delete).toHaveBeenCalledWith('new-proj-uuid');
      expect(snackBar.open).toHaveBeenCalledWith(
        'Upload failed', 'Dismiss', { duration: 5000 });
    });

    it('should set uploading flag during submission', () => {
      mockProjectService.create.and.returnValue(of(MOCK_PROJECT));
      const uploadResponse = new HttpResponse({ body: { id: 1 } });
      mockAssetService.upload.and.returnValue(of(uploadResponse as any));

      component.onSubmit();
      // After completion, uploading is reset to false
      expect(component.uploading).toBeFalse();
    });
  });
});
