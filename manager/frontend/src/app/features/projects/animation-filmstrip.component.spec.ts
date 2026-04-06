// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { SimpleChange } from '@angular/core';
import { AnimationFilmstripComponent } from './animation-filmstrip.component';
import { FilmstripFrame } from './filmstrip-frame';

function makeFrame(id: number, frameNumber: number): FilmstripFrame {
  return {
    id,
    frameNumber,
    thumbnail: `/media/thumbs/frame_${frameNumber}.png`,
    outputFile: `/media/output/frame_${frameNumber}.png`,
  };
}

describe('AnimationFilmstripComponent', () => {
  let component: AnimationFilmstripComponent;
  let fixture: ComponentFixture<AnimationFilmstripComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AnimationFilmstripComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(AnimationFilmstripComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should accept FilmstripFrame[] as input', () => {
    const frames: FilmstripFrame[] = [makeFrame(1, 1), makeFrame(2, 2)];
    component.frames = frames;
    component.ngOnChanges({
      frames: new SimpleChange([], frames, true),
    });

    expect(component.visibleFrames.length).toBe(2);
    expect(component.visibleFrames[0].frameNumber).toBe(1);
  });

  it('should paginate at 5 frames per page', () => {
    const frames = Array.from({ length: 12 }, (_, i) => makeFrame(i + 1, i + 1));
    component.frames = frames;
    component.ngOnChanges({
      frames: new SimpleChange([], frames, true),
    });

    expect(component.totalPages).toBe(3);
    expect(component.visibleFrames.length).toBe(5);
  });

  it('should emit FilmstripFrame on frame click', () => {
    const frame = makeFrame(1, 1);
    spyOn(component.frameSelected, 'emit');
    component.onFrameClick(frame);
    expect(component.frameSelected.emit).toHaveBeenCalledWith(frame);
  });

  it('should navigate pages', () => {
    const frames = Array.from({ length: 12 }, (_, i) => makeFrame(i + 1, i + 1));
    component.frames = frames;
    component.ngOnChanges({
      frames: new SimpleChange([], frames, true),
    });

    expect(component.currentPage).toBe(1);
    component.nextPage();
    expect(component.currentPage).toBe(2);
    expect(component.visibleFrames[0].frameNumber).toBe(6);

    component.prevPage();
    expect(component.currentPage).toBe(1);
    expect(component.visibleFrames[0].frameNumber).toBe(1);
  });

  it('should scroll to page containing selected frame', () => {
    const frames = Array.from({ length: 12 }, (_, i) => makeFrame(i + 1, i + 1));
    component.frames = frames;
    component.ngOnChanges({
      frames: new SimpleChange([], frames, true),
    });

    // Select frame on page 2 (frame id=8)
    component.selectedFrameId = 8;
    component.ngOnChanges({
      selectedFrameId: new SimpleChange(null, 8, false),
    });

    expect(component.currentPage).toBe(2);
  });
});
