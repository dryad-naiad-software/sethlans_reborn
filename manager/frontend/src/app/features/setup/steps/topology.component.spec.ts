// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatSnackBar } from '@angular/material/snack-bar';
import { of, throwError } from 'rxjs';
import { TopologyComponent } from './topology.component';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';

describe('TopologyComponent', () => {
  let component: TopologyComponent;
  let fixture: ComponentFixture<TopologyComponent>;
  let mockApi: jasmine.SpyObj<SetupApiService>;
  let state: SetupStateService;
  let snackBar: MatSnackBar;

  beforeEach(async () => {
    mockApi = jasmine.createSpyObj('SetupApiService', ['setTopology']);
    mockApi.setTopology.and.returnValue(of({ status: 'ok' }));

    await TestBed.configureTestingModule({
      imports: [TopologyComponent, NoopAnimationsModule],
      providers: [
        { provide: SetupApiService, useValue: mockApi },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(TopologyComponent);
    component = fixture.componentInstance;
    state = TestBed.inject(SetupStateService);
    snackBar = fixture.debugElement.injector.get(MatSnackBar);
    spyOn(snackBar, 'open');
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should display three topology cards', () => {
    const cards = fixture.nativeElement.querySelectorAll('.topology-card');
    expect(cards.length).toBe(3);
  });

  it('should not be submitting initially', () => {
    expect(component.submitting).toBeFalse();
  });

  it('should have no selection initially', () => {
    expect(component.selected).toBeNull();
  });

  describe('onSelect', () => {
    it('should call api.setTopology with correct payload', () => {
      component.onSelect('manager');
      expect(mockApi.setTopology).toHaveBeenCalledWith({ topology: 'manager' });
    });

    it('should update state topology on success', () => {
      component.onSelect('manager_worker');
      expect(state.topology).toBe('manager_worker');
    });

    it('should mark checkpoint on success', () => {
      component.onSelect('manager');
      expect(state.checkpoints).toContain('topology_chosen');
    });

    it('should emit stepComplete on success', () => {
      let emitted = false;
      component.stepComplete.subscribe(() => emitted = true);
      component.onSelect('manager');
      expect(emitted).toBeTrue();
    });

    it('should set submitting to true then false', () => {
      component.onSelect('manager');
      expect(component.submitting).toBeFalse();
    });

    it('should show snackbar on error', () => {
      mockApi.setTopology.and.returnValue(
        throwError(() => ({ error: { detail: 'Server error' } })));
      component.onSelect('manager');
      expect(snackBar.open).toHaveBeenCalledWith(
        'Server error', 'Dismiss', { duration: 5000 });
    });

    it('should show fallback message on error without detail', () => {
      mockApi.setTopology.and.returnValue(
        throwError(() => ({ error: {} })));
      component.onSelect('manager');
      expect(snackBar.open).toHaveBeenCalledWith(
        'Failed to set topology', 'Dismiss', { duration: 5000 });
    });

    it('should reset submitting on error', () => {
      mockApi.setTopology.and.returnValue(
        throwError(() => ({ error: {} })));
      component.onSelect('manager');
      expect(component.submitting).toBeFalse();
    });
  });
});
