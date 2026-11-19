# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU memory reader building blocks.

So far only the pieces the worker card selection needs: PCI address
normalisation (``pci``) and the Windows bindings for the DXGI adapter list
(``win_dxgi``) and the PCI address of an adapter (``win_adapter_info``).

This package deliberately contains no imports, so importing it loads no
DLL. The Windows modules load their system DLLs on first use only.
"""
