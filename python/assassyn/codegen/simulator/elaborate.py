"""Elaborate function for Assassyn simulator generator."""

from __future__ import annotations

import os
import shutil
import subprocess
import typing
from pathlib import Path

from .modules import dump_modules
from .simulator import dump_simulator
from .verilator import emit_external_sv_ffis

from ...utils import repo_path

if typing.TYPE_CHECKING:
    from ...builder import SysBuilder


def _resolve_paths(sys: "SysBuilder", config: dict[str, typing.Any]) -> tuple[Path, Path]:
    """Return simulator and Verilator output directories."""

    workspace = Path(config.get('path', os.getcwd()))
    simulator_dirname = (
        config.get('simulator_dirname')
        or config.get('dirname')
        or f"{sys.name}_simulator"
    )
    simulator_path = workspace / simulator_dirname
    verilator_dirname = config.get('verilator_dirname', f"{sys.name}_verilator")
    verilator_root = simulator_path / verilator_dirname
    return simulator_path, verilator_root


def _prepare_simulator_dir(simulator_path: Path, override_dump: bool) -> None:
    """Reset simulator output directory and create required structure."""

    if simulator_path.exists() and override_dump:
        shutil.rmtree(simulator_path)

    simulator_path.mkdir(parents=True, exist_ok=True)
    (simulator_path / "src").mkdir(exist_ok=True)


def _write_manifest(sys: "SysBuilder", simulator_path: Path, ffi_specs: list[typing.Any]) -> Path:
    """Emit Cargo.toml for the generated simulator project."""

    manifest_path = simulator_path / "Cargo.toml"
    runtime_path = Path(repo_path()) / "tools" / "rust-sim-runtime"

    with open(manifest_path, 'w', encoding='utf-8') as cargo:
        cargo.write("[package]\n")
        cargo.write(f'name = "{sys.name}_simulator"\n')
        cargo.write('version = "0.1.0"\n')
        cargo.write('edition = "2021"\n')
        cargo.write('[dependencies]\n')
        cargo.write(f'sim-runtime = {{ path = "{runtime_path}" }}\n')
        for spec in ffi_specs:
            rel_path = os.path.relpath(spec.crate_path, simulator_path).replace(os.sep, '/')
            cargo.write(f'{spec.crate_name} = {{ path = "{rel_path}" }}\n')

    return manifest_path


def elaborate_impl(sys: "SysBuilder", config: dict[str, typing.Any]):
    """Internal implementation of the elaborate function."""

    simulator_path, verilator_root = _resolve_paths(sys, config)

    _prepare_simulator_dir(simulator_path, config.get('override_dump', True))

    ffi_specs = emit_external_sv_ffis(sys, config, simulator_path, verilator_root)

    print(f"Writing simulator code to rust project: {simulator_path}")

    manifest_path = _write_manifest(sys, simulator_path, ffi_specs)

    rustfmt_src = Path(repo_path()) / "rustfmt.toml"
    if rustfmt_src.exists():
        shutil.copy(rustfmt_src, simulator_path / "rustfmt.toml")

    modules_dir = simulator_path / "src" / "modules"
    dump_modules(sys, modules_dir)

    with open(simulator_path / "src/simulator.rs", 'w', encoding='utf-8') as fd:
        dump_simulator(sys, config, fd)

    template_main = Path(__file__).resolve().parent / "template" / "main.rs"
    shutil.copy(template_main, simulator_path / "src/main.rs")

    return manifest_path


def elaborate(sys: "SysBuilder", **config):
    """Generate a Rust-based simulator for the given Assassyn system."""

    # pylint: disable=import-outside-toplevel
    from .port_mapper import reset_port_manager

    reset_port_manager()

    local_config = config.copy()
    local_config.setdefault('simulator_dirname', f"{sys.name}_simulator")
    local_config.setdefault('verilator_dirname', f"{sys.name}_verilator")

    manifest_path = elaborate_impl(sys, local_config)

    try:
        subprocess.run(
            ["cargo", "fmt", "--manifest-path", str(manifest_path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Warning: Failed to format code with cargo fmt")

    return manifest_path
