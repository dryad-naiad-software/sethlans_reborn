# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
A minimal fake ``bpy`` for executing generated render scripts in tests.

Only the attributes the GPU selection code touches exist: the Cycles add-on
preferences (``compute_device_type``, ``get_devices()``, ``devices``), the
active scene's ``render.engine`` and ``cycles.device``, and
``bpy.data.scenes``. Device specs are ``(type, id, name)`` tuples, in the
order Blender lists them (one entry per backend, so one RTX card appears
as a CUDA entry and an OPTIX entry).
"""
import sys
import types
from types import SimpleNamespace

from sethlans_worker_agent import gpu_device_script, render_script

# Generic devices. The OPTIX id is the CUDA id plus "_OptiX", as in Blender.
RTX_CUDA = ('CUDA', 'CUDA_NVIDIA GeForce RTX 4080_0000:01:00', 'NVIDIA GeForce RTX 4080')
RTX_OPTIX = ('OPTIX', 'CUDA_NVIDIA GeForce RTX 4080_0000:01:00_OptiX', 'NVIDIA GeForce RTX 4080')
CPU = ('CPU', 'CPU', 'Generic x86-64 CPU')
IGPU_HIP = ('HIP', 'HIP_AMD Radeon(TM) Graphics_0000:0c:00', 'AMD Radeon(TM) Graphics')

# The issue's Blender list and the worker's matching per-card list.
BLENDER_MIXED = [RTX_CUDA, CPU, RTX_OPTIX, IGPU_HIP]


class FakeDevice:
    """One entry of ``prefs.devices``. Blender enables new devices by default."""

    def __init__(self, dev_type, dev_id, name):
        self.type = dev_type
        self.id = dev_id
        self.name = name
        self.use = True


class StuckDevice(FakeDevice):
    """A device whose ``use`` flag ignores writes and keeps a fixed value."""

    def __init__(self, dev_type, dev_id, name, value):
        self._value = value
        super().__init__(dev_type, dev_id, name)

    @property
    def use(self):
        return self._value

    @use.setter
    def use(self, value):
        pass


class FakePrefs:
    """The Cycles add-on preferences.

    Args:
        specs: ``(type, id, name)`` tuples that ``get_devices()`` lists.
        stuck: ``{(type, id): value}`` for entries whose ``use`` never changes.
        rejected: backends whose assignment raises, like an enum value that
            this Blender build does not offer.
    """

    def __init__(self, specs, stuck=None, rejected=()):
        self._specs = list(specs)
        self._stuck = dict(stuck or {})
        self._rejected = set(rejected)
        self._compute_device_type = 'NONE'
        self.devices = []
        self.get_devices_calls = 0

    @property
    def compute_device_type(self):
        return self._compute_device_type

    @compute_device_type.setter
    def compute_device_type(self, value):
        if value in self._rejected:
            raise TypeError(f'enum "{value}" not found in (\'NONE\', \'CUDA\')')
        self._compute_device_type = value

    def get_devices(self):
        self.get_devices_calls += 1
        self.devices = [self._make(spec) for spec in self._specs]

    def _make(self, spec):
        dev_type, dev_id, _name = spec
        if (dev_type, dev_id) in self._stuck:
            return StuckDevice(*spec, self._stuck[(dev_type, dev_id)])
        return FakeDevice(*spec)

    def enabled(self):
        """Return ``(type, id)`` of every entry with ``use`` on."""
        return [(d.type, d.id) for d in self.devices if d.use]


def card(index, spec):
    """Return a worker card record (get_gpu_device_details) for a device spec."""
    dev_type, dev_id, name = spec
    return {'index': index, 'name': name, 'type': dev_type, 'id': dev_id}


def install_fake_bpy(monkeypatch, prefs):
    """Put a fake ``bpy`` module holding ``prefs`` into ``sys.modules``."""
    scene = SimpleNamespace(
        render=SimpleNamespace(engine=None),
        cycles=SimpleNamespace(device=None, samples=None),
    )
    cycles_addon = SimpleNamespace(preferences=prefs)
    bpy = types.ModuleType('bpy')
    bpy.context = SimpleNamespace(
        preferences=SimpleNamespace(addons={'cycles': cycles_addon}),
        scene=scene,
    )
    bpy.data = SimpleNamespace(scenes=[scene])
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    return bpy


def generate_script(mocker, cards, gpu_index, render_settings=None):
    """Generate the GPU render script for ``cards`` with detection patched."""
    mocker.patch(
        'sethlans_worker_agent.system_monitor.get_gpu_device_details',
        return_value=cards,
    )
    mocker.patch(
        'sethlans_worker_agent.system_monitor.detect_gpu_devices',
        return_value=sorted({c['type'] for c in cards}),
    )
    mocker.patch('sethlans_worker_agent.config.FORCE_GPU_INDEX', None)
    return render_script.generate_render_config_script(
        238, 'CYCLES', 'GPU', render_settings or {},
        gpu_index_override=gpu_index,
    )


def run_script(script, namespace=None):
    """Compile and execute a generated script; return its globals."""
    if namespace is None:
        namespace = {}
    namespace.setdefault('__name__', '__sethlans_render_script__')
    exec(compile(script, '<sethlans-render-script>', 'exec'), namespace)
    return namespace


def render(mocker, monkeypatch, cards, specs, gpu_index, **prefs_kwargs):
    """Generate and execute the script against a fake Blender.

    Returns ``(prefs, bpy)``. A failed device check propagates SystemExit.
    """
    prefs = FakePrefs(specs, **prefs_kwargs)
    bpy = install_fake_bpy(monkeypatch, prefs)
    run_script(generate_script(mocker, cards, gpu_index))
    return prefs, bpy


def marker_lines(text, marker):
    """Return the lines of ``text`` that start with ``marker``."""
    return [line for line in text.splitlines() if line.startswith(marker)]


def check_failures(text):
    """Return the GPU check failure lines of a stderr capture."""
    return marker_lines(text, gpu_device_script.GPU_CHECK_MARKER)


def list_mismatches(text):
    """Return the card-list mismatch lines of a stderr capture."""
    return marker_lines(text, gpu_device_script.GPU_LIST_MISMATCH_MARKER)
