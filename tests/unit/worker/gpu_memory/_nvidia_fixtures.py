# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Hand-made ``nvidia-smi -q -d PIDS`` listings for the GPU memory reader tests (C-20).

The layout follows NVIDIA's documented ``-q -d PIDS`` output: a header, ``Attached GPUs``, then one block per
card that starts with ``GPU <bus id>`` and lists ``Process ID``, ``Type``, ``Name`` and ``Used GPU Memory`` under
``Processes`` (``Processes : None`` for an idle card). Every value is made up: bus ids, pids, process names,
sizes and header values. No path, UUID or serial number.
"""

HEADER = ("\n==============NVSMI LOG==============\n\n"
          "Timestamp                                 : Thu Jan  1 00:00:00 2026\n"
          "Driver Version                            : 000.00.00\n"
          "CUDA Version                              : 00.0\n\n")


def gpu_block(bus_id, *processes):
    """One card's block; ``processes`` are ``(pid, type, name, used)`` with ``used`` as printed (``'512 MiB'``)."""
    if not processes:
        return f"GPU {bus_id}\n    Processes                             : None\n\n"
    lines = [f"GPU {bus_id}", "    Processes"]
    for pid, kind, name, used in processes:
        lines += ["        GPU instance ID                   : N/A",
                  "        Compute instance ID               : N/A",
                  f"        Process ID                        : {pid}",
                  f"            Type                          : {kind}",
                  f"            Name                          : {name}",
                  f"            Used GPU Memory               : {used}"]
    return '\n'.join(lines) + '\n\n'


def listing(*blocks):
    """A whole ``-q -d PIDS`` output with the given card blocks."""
    return HEADER + f"Attached GPUs                             : {len(blocks)}\n" + ''.join(blocks)


BUS_A = '00000000:01:00.0'
BUS_B = '00000000:02:00.0'
KEY_A = 'pci:0000:01:00.0'
KEY_B = 'pci:0000:02:00.0'

# card A: a render (ours, Vulkan, so type G) and a desktop shell; card B: one compute job.
BUSY = listing(gpu_block(BUS_A, (4100, 'G', 'render-app', '600 MiB'), (4200, 'G', 'desktop-shell', '300 MiB')),
               gpu_block(BUS_B, (4300, 'C', 'compute-job', '50 MiB')))
IDLE = listing(gpu_block(BUS_A))
