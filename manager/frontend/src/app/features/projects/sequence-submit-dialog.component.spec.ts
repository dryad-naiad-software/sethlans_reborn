// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { of, throwError } from 'rxjs';
import { SequenceService } from '../../core/services/sequence.service';
import { Project } from '../../core/services/project.service';
import { Asset } from '../../core/services/asset.service';
import { SequenceSubmitDialogComponent, SequenceSubmitDialogData } from './sequence-submit-dialog.component';
import { HOSTILE, discovery, text, version } from './sequence.fixtures';

const PROJECT: Project = {
  id: 'unreal-uuid', name: 'U', renderer: 'UNREAL', blender_version: null, blender_version_details: null,
  engine_build: '5.8.4', unreal_sound: true, created_at: '2026-11-01T00:00:00Z',
};
const ASSET: Asset = {
  id: 5, name: 'snap', blend_file: null, created_at: '2026-11-01T00:00:00Z', project: PROJECT.id, project_details: PROJECT,
};

describe('SequenceSubmitDialogComponent', () => {
  let fixture: ComponentFixture<SequenceSubmitDialogComponent>;
  let component: SequenceSubmitDialogComponent;
  let sequences: jasmine.SpyObj<SequenceService>;
  let dialogRef: jasmine.SpyObj<MatDialogRef<SequenceSubmitDialogComponent>>;
  let snackBar: jasmine.SpyObj<MatSnackBar>;

  async function setup(data: Partial<SequenceSubmitDialogData> = {}): Promise<void> {
    sequences = jasmine.createSpyObj<SequenceService>('SequenceService',
      ['list', 'listDiscoveries', 'discover', 'pollDiscovery', 'create']);
    sequences.list.and.returnValue(of([version()]));
    sequences.listDiscoveries.and.returnValue(of([]));
    sequences.discover.and.returnValue(of(discovery({ status: 'QUEUED' })));
    sequences.pollDiscovery.and.returnValue(of(discovery()));
    sequences.create.and.returnValue(of(version({ name: 'Ep1 v3' })));
    dialogRef = jasmine.createSpyObj('MatDialogRef', ['close']);
    snackBar = jasmine.createSpyObj<MatSnackBar>('MatSnackBar', ['open']);
    await TestBed.configureTestingModule({
      imports: [SequenceSubmitDialogComponent, NoopAnimationsModule],
      providers: [
        { provide: MAT_DIALOG_DATA, useValue: { project: PROJECT, assets: [ASSET], ...data } },
        { provide: MatDialogRef, useValue: dialogRef },
        { provide: SequenceService, useValue: sequences },
        { provide: MatSnackBar, useValue: snackBar },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(SequenceSubmitDialogComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  function readShots(): void {
    (fixture.nativeElement.querySelector('button.read-shots') as HTMLButtonElement).click();
    fixture.detectChanges();
  }

  it('prefills map, sequence and preset from the last version and sound from the project', async () => {
    await setup();
    const v = component.form.getRawValue();
    expect([v.map, v.sequence, v.preset]).toEqual(['/Game/Maps/Main.Main', '/Game/Seq/Ep1.Ep1', '/Game/Presets/Final.Final']);
    expect(v.asset).toBe(5);
    expect(v.sound).toBeTrue();
  });

  it('falls back to the last discovery when there is no version', async () => {
    await setup();
    sequences.list.and.returnValue(of([]));
    sequences.listDiscoveries.and.returnValue(of([discovery({ map: '/Game/Other.Other' })]));
    fixture = TestBed.createComponent(SequenceSubmitDialogComponent);
    fixture.detectChanges();
    expect(fixture.componentInstance.form.controls.map.value).toBe('/Game/Other.Other');
  });

  it('reads the shots and lists them ticked; shot names are text, not markup', async () => {
    await setup();
    sequences.pollDiscovery.and.returnValue(of(discovery({
      shots: [{ index: 1, name: HOSTILE, start: 0, end: 10 }], warnings: [HOSTILE],
    })));
    readShots();
    expect(sequences.discover).toHaveBeenCalledWith({
      asset: 5, map: '/Game/Maps/Main.Main', sequence: '/Game/Seq/Ep1.Ep1', preset: '/Game/Presets/Final.Final',
    });
    expect(sequences.pollDiscovery).toHaveBeenCalledWith(3, jasmine.any(Function));
    const el: HTMLElement = fixture.nativeElement;
    expect(text(el.querySelector('.shots'))).toContain(HOSTILE);
    expect(text(el.querySelector('.discovery-warning'))).toBe(HOSTILE);
    expect(el.querySelector('.shots img, .discovery-warning img, .shots b')).toBeNull();
    expect(component.shots).toEqual([HOSTILE]);
  });

  it('disables EXR and sound when the preset cannot make them', async () => {
    await setup();
    sequences.pollDiscovery.and.returnValue(of(discovery({
      preset_facts: { resolution: null, temporal_samples: 1, spatial_samples: 1, can_png: true, can_exr: false, can_sound: false },
    })));
    readShots();
    expect(component.form.controls.sound.disabled).toBeTrue();
    expect(fixture.nativeElement.textContent).toContain('The preset has no EXR output.');
    expect(fixture.nativeElement.textContent).toContain('The preset has no audio output.');
  });

  it('keeps every option open when the preset facts are empty ({})', async () => {
    await setup();
    sequences.pollDiscovery.and.returnValue(of(discovery({ preset_facts: {} })));
    readShots();
    expect(component.form.controls.sound.enabled).toBeTrue();
    expect(component.presetSize()).toBeNull();
    expect(fixture.nativeElement.textContent).not.toContain('The preset has no');
  });

  it('disables the video choice for EXR', async () => {
    await setup();
    component.form.controls.frameFormat.setValue('EXR');
    expect(component.form.controls.videoPreset.disabled).toBeTrue();
    component.form.controls.frameFormat.setValue('PNG');
    expect(component.form.controls.videoPreset.enabled).toBeTrue();
  });

  it('submits the ticked shots with the output and draft options and closes', async () => {
    await setup();
    readShots();
    component.form.patchValue({
      selection: 'shots', videoPreset: 'web_h264', mergeSound: true, draft: true, motionBlurOff: true,
    });
    fixture.detectChanges();
    component.submit();
    expect(sequences.create).toHaveBeenCalledWith(jasmine.objectContaining({
      asset: 5, shots: ['sh010', 'sh020'], frame_format: 'PNG', video_settings: { preset: 'web_h264' },
      sound: true, merge_sound: true, draft: { motion_blur: false },
    }));
    expect(dialogRef.close).toHaveBeenCalledWith(jasmine.objectContaining({ name: 'Ep1 v3' }));
  });

  it('does not submit an unordered frame range or a half resolution', async () => {
    await setup();
    component.form.patchValue({ selection: 'range', frameStart: 20, frameEnd: 10 });
    expect(component.canSubmit()).toBeFalse();
    component.form.patchValue({ frameStart: 10, frameEnd: 20, resolutionX: 1920 });
    expect(component.canSubmit()).toBeFalse();
    component.form.patchValue({ resolutionY: 1080 });
    expect(component.canSubmit()).toBeTrue();
  });

  it('blocks Submit with a plain message for what the API refuses', async () => {
    await setup();
    readShots();
    component.form.patchValue({ selection: 'range', frameStart: 0, frameEnd: 200 });
    fixture.detectChanges();
    expect(component.canSubmit()).toBeFalse();
    expect(text(fixture.nativeElement.querySelector('.form-error'))).toBe('The frame range is outside the sequence (0-95).');
    expect((fixture.nativeElement.querySelector('button.submit') as HTMLButtonElement).disabled).toBeTrue();
    component.form.patchValue({ frameEnd: 95 });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.form-error')).toBeNull();
    expect(component.canSubmit()).toBeTrue();
  });

  it('shows the server\'s refusal and stays open', async () => {
    await setup();
    sequences.create.and.returnValue(throwError(() => ({ error: { frame_format: ['No EXR node.'] } })));
    component.submit();
    expect(snackBar.open).toHaveBeenCalledWith('frame_format: No EXR node.', 'Dismiss', jasmine.any(Object));
    expect(dialogRef.close).not.toHaveBeenCalled();
    expect(component.submitting).toBeFalse();
  });

  it('with a shot to redo, reads the shots at once and ticks only that shot', async () => {
    await setup({ prefill: { asset: 5, map: 'm', sequence: 's', preset: 'p', shots: ['sh020'] } });
    await new Promise(resolve => setTimeout(resolve));
    fixture.detectChanges();
    expect(sequences.list).not.toHaveBeenCalled();
    expect(sequences.discover).toHaveBeenCalled();
    expect(component.form.controls.selection.value).toBe('shots');
    expect(component.shots).toEqual(['sh020']);
  });
});
