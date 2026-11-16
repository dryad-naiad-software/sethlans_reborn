# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Blender-side source of the GPU device selection and check (#238).

``SELECT_HELPER`` is pasted verbatim into every GPU render script by
``gpu_device_script``; only the call's arguments change between jobs. It
runs inside Blender, so it uses nothing but ``sys`` and the Cycles add-on
preferences it is given, and it expects these globals to be defined first:
``_SETHLANS_GPU_CHECK_MARKER``, ``_SETHLANS_GPU_LIST_MISMATCH_MARKER`` and
``_SETHLANS_GPU_CHECK_EXIT_CODE``.

``_sethlans_select_gpus(prefs, backend, mode, cards, job_cards)``:

- ``cards``: every card on the worker list, ``(id, type, name)``;
- ``job_cards``: the cards that must be found, ``(id, type)``. Isolated
  mode enables exactly these. Combined mode enables every ``backend``
  entry that belongs to a listed card (an id without a trailing ``_OptiX``
  equals a listed card's id without it), so a non-RTX NVIDIA card listed
  under CUDA still renders through OptiX next to an RTX card.

Everything it prints is ASCII (``!a`` and ``backslashreplace``), so a
device name with non-ASCII characters cannot break the output streams.
Any failure ends in ``_sethlans_gpu_check_exit``: one marker line on
stderr, then ``sys.exit`` with the check exit code before the render.
"""

# Known Cycles GPU backends, in machine-wide preference order. This module
# imports nothing, so detection and script generation can both use this
# one list without an import cycle.
KNOWN_BACKENDS = ('OPTIX', 'CUDA', 'HIP', 'METAL', 'ONEAPI')

SELECT_HELPER = r"""
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


def _sethlans_select_gpus(prefs, backend, mode, cards, job_cards):
    def label(dev_type, dev_id):
        return f'{dev_type} {dev_id!a}'

    def base(dev_id):
        return dev_id[:-6] if dev_id.endswith('_OptiX') else dev_id

    def offered():
        try:
            return ', '.join(label(d.type, d.id) for d in prefs.devices if d.type != 'CPU')
        except Exception as exc:
            return f'unavailable ({exc!a})'

    def fail(detail):
        wanted = ', '.join(label(t, i) for i, t in job_cards)
        _sethlans_gpu_check_exit(f'{detail}; wanted [{wanted}]; Blender offers [{offered()}]')

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
    job_keys = [(t, i) for i, t in job_cards]
    listed_bases = {base(i) for i, _t, _n in cards}
    for i, t, n in cards:
        if (t, i) not in devices and (t, i) not in job_keys:
            mismatch(f'worker card {label(t, i)} ({n!a}) is not in the Blender device list')
    for t, i in devices:
        if t != 'CPU' and base(i) not in listed_bases:
            mismatch(f'Blender device {label(t, i)} belongs to no card on the worker list')
    for key in job_keys:
        count = devices.count(key)
        if count != 1:
            fail(f'{count} Blender device entries match {label(*key)}')
    if mode == 'isolated':
        on_keys = job_keys
    else:
        on_keys = [(t, i) for t, i in devices if t == backend and base(i) in listed_bases]
    for d in prefs.devices:
        d.use = (d.type, d.id) in on_keys
    used = [(d.type, d.id) for d in prefs.devices if d.use]
    if (not used or sorted(used) != sorted(on_keys)
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
