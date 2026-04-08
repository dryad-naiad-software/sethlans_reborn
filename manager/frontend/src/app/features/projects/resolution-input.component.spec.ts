// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { FormGroup, FormControl, Validators } from '@angular/forms';
import { ResolutionInputComponent } from './resolution-input.component';

function createParentForm(overrides?: {
  resolutionX?: number | null;
  resolutionY?: number | null;
}): FormGroup {
  const xValue = overrides && 'resolutionX' in overrides ? overrides.resolutionX! : 1920;
  const yValue = overrides && 'resolutionY' in overrides ? overrides.resolutionY! : 1080;
  const xyValidators = [Validators.required, Validators.min(1)];
  return new FormGroup({
    resolutionMode: new FormControl<'preset' | 'custom' | null>('preset', Validators.required),
    resolutionPreset: new FormControl<string | null>('fhd1080', Validators.required),
    resolutionX: new FormControl<number | null>(xValue, xyValidators),
    resolutionY: new FormControl<number | null>(yValue, xyValidators),
  });
}

async function setup(parentForm: FormGroup): Promise<{
  component: ResolutionInputComponent;
  fixture: ComponentFixture<ResolutionInputComponent>;
}> {
  await TestBed.configureTestingModule({
    imports: [ResolutionInputComponent, NoopAnimationsModule],
  }).compileComponents();
  const fixture = TestBed.createComponent(ResolutionInputComponent);
  const component = fixture.componentInstance;
  component.parentForm = parentForm;
  fixture.detectChanges();
  return { component, fixture };
}

describe('ResolutionInputComponent', () => {
  describe('default state', () => {
    it('should default to preset mode with fhd1080 selected and parent X/Y at 1920/1080', async () => {
      const parentForm = createParentForm();
      await setup(parentForm);
      expect(parentForm.get('resolutionMode')!.value).toBe('preset');
      expect(parentForm.get('resolutionPreset')!.value).toBe('fhd1080');
      expect(parentForm.get('resolutionX')!.value).toBe(1920);
      expect(parentForm.get('resolutionY')!.value).toBe(1080);
    });

    it('should disable resolutionX and resolutionY after ngOnInit', async () => {
      const parentForm = createParentForm();
      await setup(parentForm);
      expect(parentForm.get('resolutionX')!.disabled).toBeTrue();
      expect(parentForm.get('resolutionY')!.disabled).toBeTrue();
    });
  });

  describe('preset selection mechanics', () => {
    it('should write 3840/2160 into parent X/Y when resolutionPreset changes to uhd4k', async () => {
      const parentForm = createParentForm();
      await setup(parentForm);
      parentForm.get('resolutionPreset')!.setValue('uhd4k');
      expect(parentForm.get('resolutionX')!.value).toBe(3840);
      expect(parentForm.get('resolutionY')!.value).toBe(2160);
    });

    it('should write 4096/2160 into parent X/Y when resolutionPreset changes to dci4k', async () => {
      const parentForm = createParentForm();
      await setup(parentForm);
      parentForm.get('resolutionPreset')!.setValue('dci4k');
      expect(parentForm.get('resolutionX')!.value).toBe(4096);
      expect(parentForm.get('resolutionY')!.value).toBe(2160);
    });

    it('should write 1080/1080 into parent X/Y when resolutionPreset changes to sq1080', async () => {
      const parentForm = createParentForm();
      await setup(parentForm);
      parentForm.get('resolutionPreset')!.setValue('sq1080');
      expect(parentForm.get('resolutionX')!.value).toBe(1080);
      expect(parentForm.get('resolutionY')!.value).toBe(1080);
    });
  });

  describe('mode toggle', () => {
    it('should enable resolutionX and resolutionY when mode changes to custom', async () => {
      const parentForm = createParentForm();
      await setup(parentForm);
      parentForm.get('resolutionMode')!.setValue('custom');
      expect(parentForm.get('resolutionX')!.enabled).toBeTrue();
      expect(parentForm.get('resolutionY')!.enabled).toBeTrue();
    });

    it('should disable resolutionPreset when mode changes to custom while keeping its value', async () => {
      const parentForm = createParentForm();
      await setup(parentForm);
      parentForm.get('resolutionPreset')!.setValue('uhd4k');
      parentForm.get('resolutionMode')!.setValue('custom');
      expect(parentForm.get('resolutionPreset')!.disabled).toBeTrue();
      expect(parentForm.get('resolutionPreset')!.value).toBe('uhd4k');
    });

    it('should re-apply current resolutionPreset x/y when mode changes back to preset, overwriting custom values', async () => {
      const parentForm = createParentForm();
      await setup(parentForm);
      parentForm.get('resolutionPreset')!.setValue('uhd4k');
      parentForm.get('resolutionMode')!.setValue('custom');
      parentForm.get('resolutionX')!.setValue(2000);
      parentForm.get('resolutionY')!.setValue(1500);
      parentForm.get('resolutionMode')!.setValue('preset');
      expect(parentForm.get('resolutionX')!.value).toBe(3840);
      expect(parentForm.get('resolutionY')!.value).toBe(2160);
    });

    it('should re-enable resolutionPreset when mode changes back to preset', async () => {
      const parentForm = createParentForm();
      await setup(parentForm);
      parentForm.get('resolutionMode')!.setValue('custom');
      parentForm.get('resolutionMode')!.setValue('preset');
      expect(parentForm.get('resolutionPreset')!.enabled).toBeTrue();
    });

    it('should remember the last-selected preset across a Custom round trip', async () => {
      const parentForm = createParentForm();
      await setup(parentForm);
      parentForm.get('resolutionPreset')!.setValue('uhd4k');
      parentForm.get('resolutionMode')!.setValue('custom');
      parentForm.get('resolutionX')!.setValue(2000);
      parentForm.get('resolutionY')!.setValue(1500);
      parentForm.get('resolutionMode')!.setValue('preset');
      // The last-selected preset (uhd4k) is re-applied, NOT the default 1920x1080
      expect(parentForm.get('resolutionX')!.value).toBe(3840);
      expect(parentForm.get('resolutionY')!.value).toBe(2160);
    });
  });

  describe('no spurious emissions during disable/enable round-trip (S5)', () => {
    it('should not fire presetCtrl.valueChanges across a preset->custom->preset toggle', async () => {
      const parentForm = createParentForm();
      await setup(parentForm);
      const presetCtrl = parentForm.get('resolutionPreset')!;
      const spy = jasmine.createSpy('presetValueChangesSpy');
      presetCtrl.valueChanges.subscribe(spy);
      parentForm.get('resolutionMode')!.setValue('custom');
      parentForm.get('resolutionMode')!.setValue('preset');
      expect(spy).not.toHaveBeenCalled();
    });
  });

  describe('preset change while in custom mode is a no-op (S6)', () => {
    it('should NOT modify parent X/Y when presetCtrl.setValue is called in custom mode', async () => {
      const parentForm = createParentForm();
      await setup(parentForm);
      parentForm.get('resolutionMode')!.setValue('custom');
      parentForm.get('resolutionX')!.setValue(1600);
      parentForm.get('resolutionY')!.setValue(900);
      parentForm.get('resolutionPreset')!.setValue('uhd4k');
      expect(parentForm.get('resolutionX')!.value).toBe(1600);
      expect(parentForm.get('resolutionY')!.value).toBe(900);
    });
  });

  describe('unknown preset id defensive guard (Q2)', () => {
    it('should NOT modify parent X/Y when an unknown preset id is set in preset mode', async () => {
      const parentForm = createParentForm();
      await setup(parentForm);
      parentForm.get('resolutionPreset')!.setValue('not-a-real-preset');
      expect(parentForm.get('resolutionX')!.value).toBe(1920);
      expect(parentForm.get('resolutionY')!.value).toBe(1080);
    });
  });

  describe('auto-detect on load (brainstorm decision #1)', () => {
    it('should land on preset mode with uhd4k when X/Y are pre-set to 3840/2160', async () => {
      const parentForm = createParentForm({ resolutionX: 3840, resolutionY: 2160 });
      await setup(parentForm);
      expect(parentForm.get('resolutionMode')!.value).toBe('preset');
      expect(parentForm.get('resolutionPreset')!.value).toBe('uhd4k');
    });

    it('should land on custom mode with X/Y enabled when X/Y are pre-set to 1600/900', async () => {
      const parentForm = createParentForm({ resolutionX: 1600, resolutionY: 900 });
      await setup(parentForm);
      expect(parentForm.get('resolutionMode')!.value).toBe('custom');
      expect(parentForm.get('resolutionX')!.enabled).toBeTrue();
      expect(parentForm.get('resolutionY')!.enabled).toBeTrue();
      expect(parentForm.get('resolutionPreset')!.value).toBe('fhd1080');
    });
  });

  describe('null / zero edge cases (S9)', () => {
    it('should not throw and default to custom/fhd1080 when X/Y are null', async () => {
      const parentForm = createParentForm({ resolutionX: null, resolutionY: null });
      let threw = false;
      try {
        await setup(parentForm);
      } catch {
        threw = true;
      }
      expect(threw).toBeFalse();
      expect(parentForm.get('resolutionMode')!.value).toBe('custom');
      expect(parentForm.get('resolutionPreset')!.value).toBe('fhd1080');
    });

    it('should not throw and default to custom/fhd1080 when X/Y are 0', async () => {
      const parentForm = createParentForm({ resolutionX: 0, resolutionY: 0 });
      let threw = false;
      try {
        await setup(parentForm);
      } catch {
        threw = true;
      }
      expect(threw).toBeFalse();
      expect(parentForm.get('resolutionMode')!.value).toBe('custom');
      expect(parentForm.get('resolutionPreset')!.value).toBe('fhd1080');
    });
  });

  describe('template DOM assertions', () => {
    it('should render the preset mat-select as disabled when mode is custom', async () => {
      const parentForm = createParentForm();
      const { fixture } = await setup(parentForm);
      parentForm.get('resolutionMode')!.setValue('custom');
      fixture.detectChanges();
      const matSelect = fixture.nativeElement.querySelector('mat-select');
      expect(matSelect).toBeTruthy();
      const isDisabled = matSelect.classList.contains('mat-mdc-select-disabled')
        || matSelect.classList.contains('mat-select-disabled')
        || matSelect.getAttribute('aria-disabled') === 'true';
      expect(isDisabled).toBeTrue();
    });

    it('should keep the mat-select element present in the DOM in both modes', async () => {
      const parentForm = createParentForm();
      const { fixture } = await setup(parentForm);
      let matSelect = fixture.nativeElement.querySelector('mat-select');
      expect(matSelect).toBeTruthy();
      parentForm.get('resolutionMode')!.setValue('custom');
      fixture.detectChanges();
      matSelect = fixture.nativeElement.querySelector('mat-select');
      expect(matSelect).toBeTruthy();
      parentForm.get('resolutionMode')!.setValue('preset');
      fixture.detectChanges();
      matSelect = fixture.nativeElement.querySelector('mat-select');
      expect(matSelect).toBeTruthy();
    });
  });
});
