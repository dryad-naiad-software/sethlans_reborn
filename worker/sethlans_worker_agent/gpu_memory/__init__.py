# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The GPU memory reader and its building blocks.

- ``types``: the reading types; ``reader``: backend selection and the
  process-wide reader; ``nvidia_smi``: the ``nvidia-smi`` backend.
- For the worker card selection: PCI address normalisation (``pci``) and
  the Windows bindings for the DXGI adapter list (``win_dxgi``) and the
  PCI address of an adapter (``win_adapter_info``).

This package deliberately contains no imports, so importing it loads no
DLL. The Windows modules load their system DLLs on first use only.
"""
