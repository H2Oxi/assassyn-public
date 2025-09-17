"""Elaborate Assassyn IR to Verilog."""

import os
from pathlib import Path
import shutil
from .testbench import generate_testbench
from .design import generate_design

from ...builder import SysBuilder
from ...ir.module.external import ExternalModule

from ...utils import create_and_clean_dir, repo_path

def elaborate(sys: SysBuilder, **kwargs) -> str:
    """Elaborate the system into Verilog.

    Args:
        sys: The system to elaborate
        **kwargs: Configuration options including:
            - verilog: The simulator to use ("Verilator", "VCS", or None)
            - resource_base: Path to resources
            - override_dump: Whether to override existing files
            - sim_threshold: Simulation threshold
            - idle_threshold: Idle threshold
            - random: Whether to randomize execution
            - fifo_depth: Default FIFO depth

    Returns:
        Path to the generated Verilog files
    """

    path = kwargs.get('path', os.getcwd())
    path = Path(path) / f"{sys.name}_verilog"

    create_and_clean_dir(path)

    external_sources = set()
    for module in sys.modules:
        if isinstance(module, ExternalModule) and getattr(module, 'file_path', None):
            external_sources.add(module.file_path)

    external_file_names = sorted({Path(file_name).name for file_name in external_sources})

    logs = generate_design(path / "design.py", sys)


    generate_testbench(path / "tb.py", sys, kwargs['sim_threshold'], logs, external_file_names)


    default_home = os.getenv('ASSASSYN_HOME', os.getcwd())
    resource_path = Path(default_home) / "python/assassyn/codegen/verilog"

    files_to_copy = ["fifo.sv", "trigger_counter.sv"]
    for file_name in files_to_copy:
        source_file = resource_path / file_name

        if source_file.is_file():
            destination_file = path / file_name
            shutil.copy(source_file, destination_file)
            print(f"Copied {source_file} to {destination_file}")
        else:
            print(f"Warning: Resource file not found: {source_file}")

    for file_name in external_sources:
        src_path = Path(file_name)
        if not src_path.is_absolute():
            src_path = Path(repo_path()) / file_name

        if src_path.is_file():
            destination_file = path / src_path.name
            shutil.copy(src_path, destination_file)
            print(f"Copied {src_path} to {destination_file}")
        else:
            print(f"Warning: External resource file not found: {src_path}")

    return path
