"""Elaborate function for Assassyn simulator generator."""

from __future__ import annotations

import os
import shutil
import subprocess
import typing
from pathlib import Path
from .modules import ElaborateModule
from .simulator import dump_simulator, dump_main
from .runtime import dump_runtime, dump_ramulator
from .external import generate_external_sv_crates

from ...ir.module.external import ExternalSV

if typing.TYPE_CHECKING:
    from ...builder import SysBuilder


def dump_modules(sys: SysBuilder, fd):
    """Generate the modules.rs file.

    This matches the Rust function in src/backend/simulator/elaborate.rs
    """
    # Add imports
    fd.write("""
use super::runtime::*;
use super::ramulator::*;
use super::simulator::Simulator;
use std::collections::VecDeque;
use num_bigint::{BigInt, BigUint};
use libloading::{Library, Symbol};
use std::ffi::{CString, c_char, c_float, c_longlong, c_void};
use std::sync::Arc;
    """)

    # Generate each module's implementation
    dict_modules_callback = {}
    em = ElaborateModule(sys)
    for module in sys.modules[:] + sys.downstreams[:]:
        if isinstance(module, ExternalSV):
            continue
        dict_modules_callback = em.visit_module_for_callback(module)
    required_keys = ["memory", "store", "MemUser_rdata"]
    if all(dict_modules_callback.get(k) is not None for k in required_keys):
        fd.write(f"""
extern "C" fn rust_callback(req: *mut Request, ctx: *mut c_void) {{
    unsafe {{
        let req = &*req;
        let sim: &mut Simulator = &mut *(ctx as *mut Simulator);
        let cycles = (req.depart - req.arrive) as usize;
        let stamp = sim.request_stamp_map_table
            .remove(&req.addr)
            .unwrap_or_else(|| sim.stamp);;
        sim.{dict_modules_callback.get("MemUser_rdata")}.push.push(FIFOPush::new(
            stamp + 100 * cycles,
            sim.{dict_modules_callback.
                 get("store")}.payload[req.addr as usize].clone().try_into().unwrap(),
            "{dict_modules_callback.get("memory")}",
        ));
    }}
}}
        """)
    for module in sys.modules[:] + sys.downstreams[:]:
        # Then, second time dump for real visit modules
        if isinstance(module, ExternalSV):
            continue
        module_code = em.visit_module(module)
        fd.write(module_code)

    return True


def elaborate_impl(sys, config):
    """Internal implementation of the elaborate function.

    This matches the Rust function in src/backend/simulator/elaborate.rs
    """
    # Create and clean the simulator directory
    workspace_root = Path(config.get('path', os.getcwd()))
    simulator_dirname = (
        config.get('simulator_dirname')
        or config.get('dirname')
        or f"{sys.name}_simulator"
    )
    simulator_path = workspace_root / simulator_dirname
    verilator_dirname = config.get('verilator_dirname', f"{sys.name}_verilator")
    verilator_root = workspace_root / verilator_dirname

    # Clean directory if it exists and override is enabled
    if simulator_path.exists() and config.get('override_dump', True):
        shutil.rmtree(simulator_path)

    # Create directories
    simulator_path.mkdir(parents=True, exist_ok=True)
    (simulator_path / "src").mkdir(exist_ok=True)

    external_modules = [
        module for module in sys.modules + sys.downstreams if isinstance(module, ExternalSV)
    ]
    ffi_specs = []
    if external_modules:
        ffi_specs = generate_external_sv_crates(external_modules, simulator_path, verilator_root)
    else:
        shutil.rmtree(verilator_root, ignore_errors=True)
    config['external_ffis'] = ffi_specs
    config['verilator_output_root'] = verilator_root
    config['simulator_output_root'] = simulator_path

    print(f"Writing simulator code to rust project: {simulator_path}")

    # Create Cargo.toml
    manifest_path = simulator_path / "Cargo.toml"
    with open(manifest_path, 'w', encoding="utf-8") as cargo:
        cargo.write("[package]\n")
        cargo.write(f'name = "{sys.name}_simulator"\n')
        cargo.write('version = "0.1.0"\n')
        cargo.write('edition = "2021"\n')
        cargo.write('[dependencies]\n')
        cargo.write('num-bigint = "0.4"\n')
        cargo.write('num-traits = "0.2"\n')
        cargo.write('rand = "0.8"\n')
        cargo.write('libloading = "0.7"\n')
        for spec in ffi_specs:
            rel_path = os.path.relpath(spec.crate_path, simulator_path).replace(os.sep, '/')
            cargo.write(f'{spec.crate_name} = {{ path = "{rel_path}" }}\n')

    # Create rustfmt.toml if available
    rustfmt_src = None
    rustfmt_candidates = [
        Path(__file__).parent.parent.parent.parent / "rustfmt.toml",  # repo root
        Path("/Users/were/repos/assassyn-dev/rustfmt.toml")  # hardcoded path
    ]

    for candidate in rustfmt_candidates:
        if candidate.exists():
            rustfmt_src = candidate
            break

    if rustfmt_src:
        shutil.copy(rustfmt_src, simulator_path / "rustfmt.toml")

    # Generate modules.rs
    with open(simulator_path / "src/modules.rs", 'w', encoding="utf-8") as fd:
        dump_modules(sys, fd)

    # Generate runtime.rs
    with open(simulator_path / "src/runtime.rs", 'w', encoding='utf-8') as fd:
        dump_runtime(fd)

    # Generate memory.rs
    with open(simulator_path / "src/ramulator.rs", 'w', encoding='utf-8') as fd:
        dump_ramulator(fd)

    # Generate simulator.rs
    with open(simulator_path / "src/simulator.rs", 'w', encoding='utf-8') as fd:
        dump_simulator(sys, config, fd)

    # Generate main.rs
    with open(simulator_path / "src/main.rs", 'w', encoding='utf-8') as fd:
        dump_main(fd)

    return manifest_path


def elaborate(sys, **config):
    """Generate a Rust-based simulator for the given Assassyn system.

    This function is the main entry point for simulator generation. It takes
    an Assassyn system builder and configuration options, and generates a Rust
    project that can simulate the system.

    Args:
        sys: The Assassyn system builder
        **config: Refer to ..codegen for the list of options

    Returns:
        Path to the generated Cargo.toml file
    """

    local_config = config.copy()
    local_config.setdefault('simulator_dirname', f"{sys.name}_simulator")
    local_config.setdefault('verilator_dirname', f"{sys.name}_verilator")

    # Generate the simulator
    manifest_path = elaborate_impl(sys, local_config)

    # Format the code if cargo fmt is available
    try:
        subprocess.run(
            ["cargo", "fmt", "--manifest-path", str(manifest_path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Warning: Failed to format code with cargo fmt")

    return manifest_path
