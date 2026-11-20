# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shot and camera-cut helpers for the prep script (Unreal foundation FR-UE-8, FR-UE-25; sequence rendering FR-SW-4).

Runs in UE 5.8's embedded Python, loaded by the prep script from one fixed
file (``importlib.util.spec_from_file_location``, never through
``sys.path``). Data, not a worker module: it imports only ``unreal``.

It **only reads** the sequence: shot sections (CinematicShotTrack and
SubTrack), camera cuts, the edit-to-inner frame mapping and the pilot frames.
It never sets a property or saves an asset.
"""
import unreal


def rate(frame_rate):
    return frame_rate.numerator / frame_rate.denominator


def active(section):
    try:
        return section.is_active()
    except Exception:
        return True


def shot_sections(seq):
    """(start, end, sub, inner_start, offset, inner/outer rate ratio) of every enabled shot section.

    Section bounds are edit display frames; the inner frame of edit frame f is
    ``inner_start + offset + (f - start) * ratio`` (S1 mapping, FR-UE-8 step 5).
    """
    disp = rate(seq.get_display_rate())
    out = []
    for track in seq.get_tracks():
        if not isinstance(track, (unreal.MovieSceneCinematicShotTrack, unreal.MovieSceneSubTrack)):
            continue
        for sec in track.get_sections():
            sub = sec.get_sequence()
            if sub is None or not active(sec):
                continue
            inner_rate = rate(sub.get_display_rate())
            offset = 0.0
            try:
                offset = sec.parameters.start_frame_offset.value * inner_rate / rate(sub.get_tick_resolution())
            except Exception:
                pass
            out.append((sec.get_start_frame(), sec.get_end_frame(), sub, sub.get_playback_start(), offset,
                        inner_rate / disp))
    return out


def cuts_of(seq):
    """Enabled camera-cut sections ``(start, end)`` of a sequence's own camera-cut track."""
    cuts = []
    for track in seq.get_tracks():
        if isinstance(track, unreal.MovieSceneCameraCutTrack):
            cuts += [(s.get_start_frame(), s.get_end_frame()) for s in track.get_sections() if active(s)]
    return cuts


def in_shot_cut(shot, frame):
    start, end, sub, inner_start, offset, ratio = shot
    inner = inner_start + offset + (frame - start) * ratio
    return start <= frame < end and any(cs <= inner < ce for cs, ce in cuts_of(sub))


def cut_starts(seq):
    """Edit frames at which a camera cut begins (own cuts, else via the shots)."""
    cuts = cuts_of(seq)
    if cuts:
        return sorted({s for s, _e in cuts})
    starts = set()
    for start, _end, sub, inner_start, offset, ratio in shot_sections(seq):
        for cs, _ce in cuts_of(sub):
            starts.add(int(round(max(start, start + (cs - inner_start - offset) / ratio))))
    return sorted(starts)


def frame_in_cut(seq, frame):
    cuts = cuts_of(seq)
    if cuts:
        return any(s <= frame < e for s, e in cuts)
    return any(in_shot_cut(shot, frame) for shot in shot_sections(seq))


def pilot_frames(seq, first, last):
    """FR-UE-25: the first qualifying frame of every cut/shot overlapping [first, last)."""
    frames = set()
    cuts = cuts_of(seq)
    if cuts:
        for s, e in cuts:
            if e > first and s < last:
                frames.add(max(s, first))
        return sorted(frames)
    for shot in shot_sections(seq):
        start, end = shot[0], shot[1]
        lo, hi = max(start, first), min(end, last)
        steps = 0
        for f in range(lo, hi):
            steps += 1
            if steps > 1000000:
                break
            if in_shot_cut(shot, f):
                frames.add(f)
                break
    return sorted(frames)
