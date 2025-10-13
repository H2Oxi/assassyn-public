"""Helpers for integrating Verilator-generated FFIs with the simulator."""

from __future__ import annotations

import shutil
import typing
from pathlib import Path

from .external import generate_external_sv_crates

from ...ir.module.external import ExternalSV

if typing.TYPE_CHECKING:
    from ...builder import SysBuilder


def emit_external_sv_ffis(
    sys: "SysBuilder",
    config: dict[str, typing.Any],
    simulator_path: Path,
    verilator_root: Path,
) -> list[typing.Any]:
    """Generate Verilator crates for ExternalSV modules when present.

    Returns the list of FFI specs recorded in config['external_ffis'].
    """

    modules = [
        module
        for module in getattr(sys, "modules", [])
        + getattr(sys, "downstreams", [])
        if isinstance(module, ExternalSV)
    ]

    if not modules:
        shutil.rmtree(verilator_root, ignore_errors=True)
        sys._external_ffi_specs = {}  # pylint: disable=protected-access
        config['external_ffis'] = []
        return []

    ffi_specs = generate_external_sv_crates(modules, simulator_path, verilator_root)
    sys._external_ffi_specs = {  # pylint: disable=protected-access
        spec.original_module_name: spec for spec in ffi_specs
    }
    config['external_ffis'] = ffi_specs
    return ffi_specs


__all__ = ["emit_external_sv_ffis"]
