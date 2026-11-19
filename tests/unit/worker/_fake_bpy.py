# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
A minimal fake ``bpy`` for executing generated render scripts in tests.

Only the attributes the generated scripts touch exist: the Cycles add-on
preferences (``compute_device_type``, ``get_devices()``, ``devices``) and
``bpy.data.scenes``, whose scenes (``StrictStruct``) carry the render,
image and Cycles settings the worker and manager send, with factory
defaults; any other scene attribute raises ``AttributeError`` as in
Blender. Device specs are ``(type, id, name)`` tuples, in the
order Blender lists them (one entry per backend, so one RTX card appears
as a CUDA entry and an OPTIX entry).
"""
import sys
import types
from types import SimpleNamespace

from sethlans_worker_agent import gpu_device_script, render_script
from sethlans_worker_agent.capacity.profile import SlotReservation
from sethlans_worker_agent.cards.in_use import FAMILIES, CardInUse
from tests.unit.worker._fake_image_settings import FakeImageSettings

# Generic devices. The OPTIX id is the CUDA id plus "_OptiX", as in Blender.
RTX_CUDA = ('CUDA', 'CUDA_NVIDIA GeForce RTX 4080_0000:01:00', 'NVIDIA GeForce RTX 4080')
RTX_OPTIX = ('OPTIX', 'CUDA_NVIDIA GeForce RTX 4080_0000:01:00_OptiX', 'NVIDIA GeForce RTX 4080')
CPU = ('CPU', 'CPU', 'Generic x86-64 CPU')
IGPU_HIP = ('HIP', 'HIP_AMD Radeon(TM) Graphics_0000:0c:00', 'AMD Radeon(TM) Graphics')

# The issue's Blender list and the worker's matching per-card list.
BLENDER_MIXED = [RTX_CUDA, CPU, RTX_OPTIX, IGPU_HIP]

# Apple silicon: a Metal id carries no PCI address (D9). Intel oneAPI:
# ONEAPI_<platform>_<name>_<SYCL ext_intel_pci_address>, with a function digit.
METAL_GPU = ('METAL', 'METAL_Apple M2 Max', 'Apple M2 Max')
ARC_ONEAPI = ('ONEAPI', 'ONEAPI_Intel(R) Level-Zero_Intel(R) Arc(TM) A770 Graphics_0000:03:00.0',
              'Intel(R) Arc(TM) A770 Graphics')


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


class StrictStruct:
    """Like a ``bpy_struct``: only known attributes can be read or set.

    Reading or assigning any other attribute raises ``AttributeError`` with
    Blender's wording, so a generated line for a missing property fails here
    as it does in Blender.
    """

    def __init__(self, type_name, **fields):
        object.__setattr__(self, '_type_name', type_name)
        object.__setattr__(self, '_fields', dict(fields))

    def _missing(self, name):
        return AttributeError(f"'{self._type_name}' object has no attribute '{name}'")

    def __getattr__(self, name):
        fields = object.__getattribute__(self, '_fields')
        if name in fields:
            return fields[name]
        raise self._missing(name)

    def __setattr__(self, name, value):
        if name not in self._fields:
            raise self._missing(name)
        self._fields[name] = value


# render.engine identifiers each Blender line accepts (official builds, #242).
ENGINES_4_1 = frozenset({'CYCLES', 'BLENDER_EEVEE', 'BLENDER_WORKBENCH'})
ENGINES_4_2_TO_4_5 = frozenset({'CYCLES', 'BLENDER_EEVEE_NEXT', 'BLENDER_WORKBENCH'})
ENGINES_5_X = frozenset({'CYCLES', 'BLENDER_EEVEE', 'BLENDER_WORKBENCH'})


class RenderStruct(StrictStruct):
    """``RenderSettings`` whose ``engine`` accepts only some identifiers."""

    def __init__(self, accepted_engines, **fields):
        object.__setattr__(self, '_accepted_engines', frozenset(accepted_engines))
        super().__init__('RenderSettings', **fields)

    def __setattr__(self, name, value):
        if name == 'engine' and value not in self._accepted_engines:
            raise TypeError(
                f'bpy_struct: item.attr = val: enum "{value}" not found in '
                f'{tuple(sorted(self._accepted_engines))}')
        super().__setattr__(name, value)


def make_scene(name='Scene', cycles_feature_set=False, engines=ENGINES_5_X, image_settings=None):
    """A scene with factory-default render settings.

    ``cycles_feature_set`` adds Cycles' ``feature_set`` (Blender 4.x has it,
    5.x does not, #241). ``engines`` is the set of ``render.engine``
    identifiers this Blender accepts (default: 5.x). ``image_settings``
    defaults to a 4.x-flavour ``FakeImageSettings`` (#243).
    """
    cycles = {'device': None, 'samples': None}
    if cycles_feature_set:
        cycles['feature_set'] = 'SUPPORTED'
    render = RenderStruct(
        engines, engine=None, resolution_x=1920, resolution_y=1080,
        resolution_percentage=100, use_border=False, use_crop_to_border=False,
        border_min_x=0.0, border_max_x=1.0, border_min_y=0.0, border_max_y=1.0,
        image_settings=image_settings or FakeImageSettings(),
    )
    return StrictStruct(
        'Scene', name=name, render=render,
        cycles=StrictStruct('CyclesRenderSettings', **cycles),
    )


def install_fake_bpy(monkeypatch, prefs, scenes=None):
    """Put a fake ``bpy`` module holding ``prefs`` into ``sys.modules``.

    ``scenes`` (default: one ``make_scene()``) become ``bpy.data.scenes``;
    the first is the context scene.
    """
    scenes = list(scenes) if scenes else [make_scene()]
    scene = scenes[0]
    cycles_addon = SimpleNamespace(preferences=prefs)
    bpy = types.ModuleType('bpy')
    bpy.context = SimpleNamespace(
        preferences=SimpleNamespace(addons={'cycles': cycles_addon}),
        scene=scene,
    )
    bpy.data = SimpleNamespace(scenes=scenes)
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    return bpy


def reservation_for(cards, gpu_index):
    """The slot reservation for worker card records ``cards``.

    An index is a split slot on that card with every card in use. ``None``
    is a combined slot over the cards of the preferred backend's family;
    the other cards are detected but not in use (``ignored``).
    """
    in_use = [CardInUse(f'pci:0000:{i + 1:02x}:00.0', c.get('name'), c.get('id'), c.get('type'))
              for i, c in enumerate(cards)]
    if gpu_index is not None:
        own = [in_use[gpu_index]] if isinstance(gpu_index, int) and 0 <= gpu_index < len(in_use) else []
        return SlotReservation(job_id=238, device_used='GPU', card_keys=tuple(c.key for c in own),
                               cards=tuple(in_use))
    backend = gpu_device_script.choose_backend({c.backend for c in in_use})
    family = FAMILIES.get(backend)
    own = [c for c in in_use if FAMILIES.get(c.backend) == family]
    ignored = tuple(c.blender_id for c in in_use if c not in own)
    return SlotReservation(job_id=238, device_used='GPU', card_keys=tuple(c.key for c in own),
                           cards=tuple(own), gpu_mode='combined', ignored=ignored)


def generate_script(mocker, cards, gpu_index, render_settings=None):
    """Generate the GPU render script for ``cards`` (see ``reservation_for``)."""
    return render_script.generate_render_config_script(
        238, 'CYCLES', 'GPU', render_settings or {},
        reservation=reservation_for(cards, gpu_index),
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


def render_reservation(monkeypatch, reservation, specs):
    """Run the GPU render script for ``reservation`` against a Blender listing ``specs``.

    Returns ``(prefs, bpy)``. A failed device check propagates SystemExit.
    """
    prefs = FakePrefs(specs)
    bpy = install_fake_bpy(monkeypatch, prefs)
    run_script(render_script.generate_render_config_script(238, 'CYCLES', 'GPU', {}, reservation=reservation))
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
