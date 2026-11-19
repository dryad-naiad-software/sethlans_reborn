# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Blender-side source of the GPU device selection and check (#238, FR-CB-4).

``SELECT_HELPER`` is pasted verbatim into every GPU render script by
``gpu_device_script``; only the call's arguments change between jobs. It
runs inside Blender, so it uses nothing but ``re``, ``sys`` and the Cycles
add-on preferences it is given, and it expects these globals first:
``_SETHLANS_GPU_CHECK_MARKER``, ``_SETHLANS_GPU_LIST_MISMATCH_MARKER``,
``_SETHLANS_GPU_CHECK_EXIT_CODE`` and ``sys``.

Entries are matched by **backend and place**, never by the full id, so a
Blender version that names a card differently still finds it (B2). The
place is the PCI address at the end of the id (``_sethlans_pci``, the rule
of ``cards.identity.blender_pci``, compared as integers); a Metal entry has
none and is matched by count (D9, B3); an id without an address is matched
by itself without a trailing ``_OptiX``.

``_sethlans_select_gpus(prefs, backend, mode, cards, job_cards, ignored)``:

- ``cards``: every card in use, ``(id, type, name)``;
- ``job_cards``: the cards that must be found, ``(id, type)``. Isolated
  mode enables exactly these. Combined mode also needs every card in use
  to have exactly one ``backend`` entry, and enables those;
- ``ignored``: Blender ids of detected cards that are not in use. Every
  entry at their place is switched off and never reported (FR-CB-2); a
  card in use at the same place wins.

Every wanted (backend, place) must match exactly one entry, else the job
fails (two entries at one address, or 0 or 2+ Metal entries). Everything it
prints is ASCII (``!a`` and ``backslashreplace``). Any failure ends in
``_sethlans_gpu_check_exit``: one marker line on stderr, then ``sys.exit``
with the check exit code before the render.
"""

# Known Cycles GPU backends, in machine-wide preference order. This module
# imports nothing, so detection and script generation can both use this
# one list without an import cycle.
KNOWN_BACKENDS = ('OPTIX', 'CUDA', 'HIP', 'METAL', 'ONEAPI')

SELECT_HELPER = r"""
import re


def _sethlans_gpu_check_exit(text):
    try:
        sys.stdout.flush()
    except BaseException:
        pass
    try:
        # Leading newline: the marker must start a line even if native code
        # has just written part of one (empty lines are ignored downstream).
        sys.stderr.write(f'\n{_SETHLANS_GPU_CHECK_MARKER} {text}\n')
        sys.stderr.flush()
    except BaseException:
        pass
    sys.exit(_SETHLANS_GPU_CHECK_EXIT_CODE)


def _sethlans_pci(dev_id):
    # (domain, bus, device) as integers from the id's last part, else None.
    text = dev_id[:-6] if dev_id.endswith('_OptiX') else dev_id
    found = re.fullmatch(r'([0-9a-f]{4,8}):([0-9a-f]{2}):([0-9a-f]{2})(?:\.[0-7])?',
                         text.rsplit('_', 1)[-1], re.IGNORECASE)
    return tuple(int(part, 16) for part in found.groups()) if found else None


def _sethlans_place(dev_type, dev_id):
    # Metal by count (D9); else the PCI address; else the id without _OptiX.
    # dev_type None (an ignored id) is Metal when the id says so.
    if dev_type == 'METAL' or (dev_type is None and dev_id.startswith('METAL_')):
        return ('metal',)
    address = _sethlans_pci(dev_id)
    if address is not None:
        return ('pci',) + address
    return ('id', dev_id[:-6] if dev_id.endswith('_OptiX') else dev_id)


def _sethlans_select_gpus(prefs, backend, mode, cards, job_cards, ignored=()):
    place = _sethlans_place

    def label(dev_type, dev_id):
        return f'{dev_type} {dev_id!a}'

    def where(spot):
        if spot[0] == 'pci':
            return 'same backend at PCI address %04x:%02x:%02x' % spot[1:]
        return 'Metal is matched by count' if spot[0] == 'metal' else 'same backend and id'

    def offered():
        try:
            return ', '.join(label(d.type, d.id) for d in prefs.devices if d.type != 'CPU')
        except Exception as exc:
            return f'unavailable ({exc!a})'

    def fail(detail):
        wanted_text = ', '.join(label(t, i) for i, t in job_cards)
        _sethlans_gpu_check_exit(f'{detail}; wanted [{wanted_text}]; Blender offers [{offered()}]')

    def mismatch(text):
        sys.stderr.write(f'{_SETHLANS_GPU_LIST_MISMATCH_MARKER} {text}\n')
        sys.stderr.flush()

    try:
        prefs.compute_device_type = backend
        refresh = getattr(prefs, 'refresh_devices', None) or prefs.get_devices
        refresh()
        devices = [(d.type, d.id) for d in prefs.devices]
    except Exception as exc:
        fail(f'could not select compute backend {backend} ({exc!a})')
    keys = [(t, place(t, i)) for t, i in devices]
    listed = {place(t, i) for i, t, _n in cards}
    ignored_places = {place(None, i) for i in ignored} - listed  # a card in use is never ignored
    wanted = {}  # (backend, place) -> label; each must match exactly one entry
    for i, t in job_cards:
        wanted.setdefault((t, place(t, i)), label(t, i))
    if mode != 'isolated':
        for i, t, _n in cards:
            wanted.setdefault((backend, place(t, i)), label(backend, i))
    wanted_places = {spot for _t, spot in wanted}
    for i, t, n in cards:
        if (t, place(t, i)) not in keys and place(t, i) not in wanted_places:
            mismatch(f'worker card {label(t, i)} ({n!a}) is not in the Blender device list')
    for (t, i), (_t, spot) in zip(devices, keys):
        if t != 'CPU' and spot not in listed and spot not in ignored_places:
            mismatch(f'Blender device {label(t, i)} belongs to no card on the worker list')
    for key, text in wanted.items():
        count = keys.count(key)
        if count != 1:
            fail(f'{count} Blender device entries match {text} ({where(key[1])})')
    on_keys = list(wanted)
    for d in prefs.devices:
        d.use = (d.type, place(d.type, d.id)) in on_keys
    used = [(d.type, d.id) for d in prefs.devices if d.use]
    if (not used or len(used) != len(on_keys) or {(t, place(t, i)) for t, i in used} != set(on_keys)
            or any(t != prefs.compute_device_type for t, _i in used)):
        enabled = ', '.join(label(*k) for k in used)
        fail(f'enabled [{enabled}] with compute type {prefs.compute_device_type}')
    has_active_device = getattr(prefs, 'has_active_device', None)
    if has_active_device is not None and not has_active_device():
        fail('Blender reports no active device (has_active_device() is False)')
    names = ', '.join(f'{d.name} ({label(d.type, d.id)})' for d in prefs.devices if d.use)
    names = names.encode('ascii', 'backslashreplace').decode('ascii')
    if mode == 'isolated':
        print(f'Isolating GPU: {names}')
    else:
        print(f'Using compute backend {backend} on: {names}')
"""
