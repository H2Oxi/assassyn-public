"""Helpers for generating Verilator FFI crates for ExternalSV modules."""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ...ir.module.external import ExternalSV
from ...ir.module.module import Wire
from ...ir.dtype import DType
from ...utils import namify, repo_path
from .utils import camelize

_C_INT_TYPES_UNSIGNED = {8: "uint8_t", 16: "uint16_t", 32: "uint32_t", 64: "uint64_t"}
_C_INT_TYPES_SIGNED = {8: "int8_t", 16: "int16_t", 32: "int32_t", 64: "int64_t"}
_RUST_INT_TYPES_UNSIGNED = {8: "u8", 16: "u16", 32: "u32", 64: "u64"}
_RUST_INT_TYPES_SIGNED = {8: "i8", 16: "i16", 32: "i32", 64: "i64"}

@dataclass
class FFIPort:
    """Description of a single ExternalSV port used for FFI generation."""

    name: str
    direction: str
    dtype: DType
    bits: int
    signed: bool
    c_type: str
    rust_type: str


@dataclass
class ExternalFFIModule:  # pylint: disable=too-many-instance-attributes
    """Artifacts emitted for a single ExternalSV module."""

    crate_name: str
    crate_path: Path
    symbol_prefix: str
    dynamic_lib_name: str
    top_module: str
    sv_filename: str
    sv_rel_path: str
    inputs: List[FFIPort] = field(default_factory=list)
    outputs: List[FFIPort] = field(default_factory=list)
    has_clock: bool = False
    has_reset: bool = False
    original_module_name: str = ""
    struct_name: str = ""
    definitions: Dict[str, str] = field(default_factory=dict)
    lib_filename: str = ""
    lib_path: Optional[Path] = None


def _storage_width(bits: int) -> int:
    if bits <= 8:
        return 8
    if bits <= 16:
        return 16
    if bits <= 32:
        return 32
    if bits <= 64:
        return 64
    raise NotImplementedError(
        f"ExternalSV wires wider than 64 bits are not yet supported (requested {bits} bits)"
    )


def _dtype_to_port(name: str, wire: Wire) -> FFIPort:
    dtype = wire.dtype
    bits = getattr(dtype, 'bits', None)
    if bits is None:
        raise ValueError(f"Wire '{name}' lacks a bit-width definition")
    storage_bits = _storage_width(bits)
    signed = dtype.is_signed()
    if signed:
        c_type = _C_INT_TYPES_SIGNED[storage_bits]
        rust_type = _RUST_INT_TYPES_SIGNED[storage_bits]
    else:
        c_type = _C_INT_TYPES_UNSIGNED[storage_bits]
        rust_type = _RUST_INT_TYPES_UNSIGNED[storage_bits]
    return FFIPort(
        name=namify(name),
        direction=wire.direction or 'input',
        dtype=dtype,
        bits=bits,
        signed=signed,
        c_type=c_type,
        rust_type=rust_type,
    )


def external_handle_field(module_name: str) -> str:
    """Return the simulator struct field name for an ExternalSV handle."""

    return f"{namify(module_name)}_ffi"


def _ensure_repo_local_path(file_path: str) -> Path:
    src = Path(file_path)
    if src.is_absolute():
        return src
    return Path(repo_path()) / src


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def _dynamic_lib_suffix() -> str:
    system = platform.system().lower()
    if system == "windows":
        return ".dll"
    if system == "darwin":
        return ".dylib"
    return ".so"


def _compiler_command() -> List[str]:
    compiler_env = os.environ.get("CXX")
    if compiler_env:
        tokens = shlex.split(compiler_env)
        if tokens:
            return tokens
    for candidate in ("clang++", "g++", "c++"):
        path = shutil.which(candidate)
        if path:
            return [path]
    raise RuntimeError(
        "Unable to locate a C++ compiler. Set the CXX environment variable or install clang++/g++."
    )


def _run_subprocess(cmd: List[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, check=True, cwd=cwd, env=os.environ.copy())


def _generate_cargo_toml(crate: ExternalFFIModule) -> str:
    return f"""[package]
name = \"{crate.crate_name}\"
version = \"0.1.0\"
edition = \"2021\"
[dependencies]
libloading = \"0.8\"
"""


def _generate_lib_rs(crate: ExternalFFIModule) -> str:
    # pylint: disable=too-many-branches, too-many-locals, too-many-statements
    struct_name = camelize(crate.symbol_prefix) or "ExternalModule"
    struct_name = struct_name[0].upper() + struct_name[1:]
    crate.struct_name = struct_name
    prefix = crate.symbol_prefix

    lines = [
        "#![allow(dead_code)]",
        "use libloading::Library;",
        "use std::path::{Path, PathBuf};",
        "use std::ptr::NonNull;",
        "",
        "#[repr(C)]",
        "#[allow(non_camel_case_types)]",
        "pub struct ModuleHandle { _private: [u8; 0] }",
        "",
        "const LIB_PATH: &str = include_str!(concat!(env!(\"CARGO_MANIFEST_DIR\"), \"/.verilator-lib-path\"));",
        "",
        "fn lib_path() -> PathBuf {",
        "    PathBuf::from(LIB_PATH.trim())",
        "}",
        "",
        "fn load_library<P: AsRef<Path>>(path: P) -> Library {",
        "    let path = path.as_ref();",
        f"    unsafe {{ Library::new(path) }}.unwrap_or_else(|err| panic!(\"failed to load Verilator library '{prefix}': {{err}} ({{}})\", path.display()))",
        "}",
        "",
        "unsafe fn load_symbol<T: Copy>(lib: &Library, symbol: &[u8], name: &str) -> T {",
        "    *lib.get::<T>(symbol).unwrap_or_else(|err| panic!(\"failed to load symbol {name}: {err}\"))",
        "}",
        "",
        f"pub struct {struct_name} {{",
        "    lib: Library,",
        "    handle: NonNull<ModuleHandle>,",
        "    free_fn: unsafe extern \"C\" fn(*mut ModuleHandle),",
        "    eval_fn: unsafe extern \"C\" fn(*mut ModuleHandle),",
    ]

    if crate.has_clock:
        lines.append("    set_clk_fn: unsafe extern \"C\" fn(*mut ModuleHandle, u8),")
        lines.append("    clk_state: u8,")
    if crate.has_reset:
        lines.append("    set_rst_fn: unsafe extern \"C\" fn(*mut ModuleHandle, u8),")
        lines.append("    rst_state: u8,")
    for port in crate.inputs:
        lines.append(
            f"    set_{port.name}_fn: unsafe extern \"C\" fn(*mut ModuleHandle, {port.rust_type}),"
        )
    for port in crate.outputs:
        lines.append(
            f"    get_{port.name}_fn: unsafe extern \"C\" fn(*mut ModuleHandle) -> {port.rust_type},"
        )
    lines.append("}")
    lines.append("")

    impl_lines = [
        f"impl {struct_name} {{",
        "    pub fn new() -> Self {",
        "        let path = lib_path();",
        "        Self::new_from_path(path)",
        "    }",
        "",
        "    pub fn new_from_path<P: AsRef<Path>>(path: P) -> Self {",
        "        let lib = load_library(path);",
        "        unsafe {",
        f"            let new_fn: unsafe extern \"C\" fn() -> *mut ModuleHandle = load_symbol(&lib, b\"{prefix}_new\", \"{prefix}_new\");",
        f"            let free_fn: unsafe extern \"C\" fn(*mut ModuleHandle) = load_symbol(&lib, b\"{prefix}_free\", \"{prefix}_free\");",
        f"            let eval_fn: unsafe extern \"C\" fn(*mut ModuleHandle) = load_symbol(&lib, b\"{prefix}_eval\", \"{prefix}_eval\");",
    ]

    if crate.has_clock:
        impl_lines.append(
            f"            let set_clk_fn: unsafe extern \"C\" fn(*mut ModuleHandle, u8) = load_symbol(&lib, b\"{prefix}_set_clk\", \"{prefix}_set_clk\");"
        )
    if crate.has_reset:
        impl_lines.append(
            f"            let set_rst_fn: unsafe extern \"C\" fn(*mut ModuleHandle, u8) = load_symbol(&lib, b\"{prefix}_set_rst\", \"{prefix}_set_rst\");"
        )
    for port in crate.inputs:
        impl_lines.append(
            f"            let set_{port.name}_fn: unsafe extern \"C\" fn(*mut ModuleHandle, {port.rust_type}) = load_symbol(&lib, b\"{prefix}_set_{port.name}\", \"{prefix}_set_{port.name}\");"
        )
    for port in crate.outputs:
        impl_lines.append(
            f"            let get_{port.name}_fn: unsafe extern \"C\" fn(*mut ModuleHandle) -> {port.rust_type} = load_symbol(&lib, b\"{prefix}_get_{port.name}\", \"{prefix}_get_{port.name}\");"
        )
    impl_lines.append(
        f"            let handle = NonNull::new(new_fn()).unwrap_or_else(|| panic!(\"{prefix}_new returned null\"));"
    )
    impl_lines.append("            let mut instance = Self {")
    impl_lines.append("                lib,")
    impl_lines.append("                handle,")
    impl_lines.append("                free_fn,")
    impl_lines.append("                eval_fn,")
    if crate.has_clock:
        impl_lines.append("                set_clk_fn,")
        impl_lines.append("                clk_state: 0,")
    if crate.has_reset:
        impl_lines.append("                set_rst_fn,")
        impl_lines.append("                rst_state: 0,")
    for port in crate.inputs:
        impl_lines.append(f"                set_{port.name}_fn,")
    for port in crate.outputs:
        impl_lines.append(f"                get_{port.name}_fn,")
    impl_lines.append("            };")
    if crate.has_clock:
        impl_lines.append("            set_clk_fn(instance.handle.as_ptr(), 0);")
    if crate.has_reset:
        impl_lines.append("            set_rst_fn(instance.handle.as_ptr(), 0);")
    impl_lines.append("            instance")
    impl_lines.append("        }")
    impl_lines.append("    }")
    impl_lines.append("")
    impl_lines.append(
        "    pub fn eval(&mut self) { unsafe { (self.eval_fn)(self.handle.as_ptr()) } }"
    )
    impl_lines.append("")

    if crate.has_clock:
        impl_lines.extend(
            [
                "    pub fn set_clock(&mut self, value: bool) {",
                "        let value = value as u8;",
                "        unsafe { (self.set_clk_fn)(self.handle.as_ptr(), value) };",
                "        self.clk_state = value;",
                "    }",
                "",
                "    pub fn clock_tick(&mut self) {",
                "        self.set_clock(false);",
                "        self.eval();",
                "        self.set_clock(true);",
                "        self.eval();",
                "    }",
                "",
            ]
        )
    if crate.has_reset:
        impl_lines.extend(
            [
                "    pub fn set_reset(&mut self, value: bool) {",
                "        let value = value as u8;",
                "        unsafe { (self.set_rst_fn)(self.handle.as_ptr(), value) };",
                "        self.rst_state = value;",
                "    }",
                "",
            ]
        )
        if crate.has_clock:
            impl_lines.extend(
                [
                    "    pub fn apply_reset(&mut self, cycles: usize) {",
                    "        self.set_reset(true);",
                    "        for _ in 0..cycles.max(1) {",
                    "            self.clock_tick();",
                    "        }",
                    "        self.set_reset(false);",
                    "        self.clock_tick();",
                    "    }",
                    "",
                ]
            )
        else:
            impl_lines.extend(
                [
                    "    pub fn apply_reset(&mut self, cycles: usize) {",
                    "        let _ = cycles;",
                    "        self.set_reset(true);",
                    "        self.eval();",
                    "        self.set_reset(false);",
                    "        self.eval();",
                    "    }",
                    "",
                ]
            )

    for port in crate.inputs:
        impl_lines.extend(
            [
                f"    pub fn set_{port.name}(&mut self, value: {port.rust_type}) {{",
                f"        unsafe {{ (self.set_{port.name}_fn)(self.handle.as_ptr(), value) }};",
                "    }",
                "",
            ]
        )
    for port in crate.outputs:
        impl_lines.extend(
            [
                f"    pub fn get_{port.name}(&mut self) -> {port.rust_type} {{",
                f"        unsafe {{ (self.get_{port.name}_fn)(self.handle.as_ptr()) }}",
                "    }",
                "",
            ]
        )
    impl_lines.append("}")
    lines.extend(impl_lines)
    lines.append("")
    lines.append(f"impl Drop for {struct_name} {{")
    lines.append(
        "    fn drop(&mut self) { unsafe { (self.free_fn)(self.handle.as_ptr()) } }"
    )
    lines.append("}")

    return "\n".join(lines)


def _generate_wrapper_cpp(crate: ExternalFFIModule) -> str:
    cpp_class = f"V{crate.top_module}"
    prefix = crate.symbol_prefix
    lines = [
        f"#include \"{cpp_class}.h\"",
        "#include \"verilated.h\"",
        "#include <cstdint>",
        "",
        "double sc_time_stamp() { return 0.0; }",
        "",
        "extern \"C\" {",
        "",
        f"using ModuleHandle = {cpp_class};",
        "",
        f"ModuleHandle* {prefix}_new() {{",
        "    static bool inited = false;",
        "    if (!inited) { Verilated::debug(0); inited = true; }",
        "    return new ModuleHandle();",
        "}",
        "",
        f"void {prefix}_free(ModuleHandle* handle) {{ delete handle; }}",
        "",
        f"void {prefix}_eval(ModuleHandle* handle) {{ handle->eval(); }}",
    ]
    if crate.has_clock:
        lines.extend(
            [
                f"void {prefix}_set_clk(ModuleHandle* handle, uint8_t value) {{",
                "    handle->clk = static_cast<uint8_t>(value & 0x1U);",
                "}",
            ]
        )
    if crate.has_reset:
        lines.extend(
            [
                f"void {prefix}_set_rst(ModuleHandle* handle, uint8_t value) {{",
                "    handle->rst = static_cast<uint8_t>(value & 0x1U);",
                "}",
            ]
        )
    for port in crate.inputs:
        lines.extend(
            [
                f"void {prefix}_set_{port.name}(ModuleHandle* handle, {port.c_type} value) {{",
                f"    handle->{port.name} = static_cast<{port.c_type}>(value);",
                "}",
            ]
        )
    for port in crate.outputs:
        lines.extend(
            [
                f"{port.c_type} {prefix}_get_{port.name}(ModuleHandle* handle) {{",
                f"    return static_cast<{port.c_type}>(handle->{port.name});",
                "}",
            ]
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def _build_verilator_library(crate: ExternalFFIModule) -> Path:
    """Compile the Verilator-generated model and wrapper into a shared library."""

    crate_path = crate.crate_path
    sv_source = crate_path / crate.sv_rel_path
    if not sv_source.exists():
        raise FileNotFoundError(f"SystemVerilog source not found: {sv_source}")

    build_root = crate_path / "build"
    obj_dir = build_root / "verilated"
    shutil.rmtree(build_root, ignore_errors=True)
    obj_dir.mkdir(parents=True, exist_ok=True)

    verilator_exe = os.environ.get("ASSASSYN_VERILATOR", "verilator")
    verilator_cmd = [
        verilator_exe,
        "--cc",
        str(sv_source),
        "--top-module",
        crate.top_module,
        "-O3",
        "--Mdir",
        str(obj_dir),
    ]
    _run_subprocess(verilator_cmd)

    verilator_root = os.environ.get("VERILATOR_ROOT")
    if not verilator_root:
        raise EnvironmentError(
            "VERILATOR_ROOT is not set. Please run 'source setup.sh' before generating external FFIs."
        )
    verilator_root_path = Path(verilator_root)
    include_dir = verilator_root_path / "include"
    vltstd_dir = include_dir / "vltstd"
    if not include_dir.exists():
        raise FileNotFoundError(f"Verilator include directory not found: {include_dir}")

    cpp_class = f"V{crate.top_module}"
    aggregated = obj_dir / f"{cpp_class}__ALL.cpp"

    source_files: List[Path] = []
    if aggregated.exists():
        source_files.append(aggregated)
    else:
        for path in sorted(obj_dir.glob("*.cpp")):
            if path.name.endswith("__ALL.cpp"):
                continue
            source_files.append(path)

    wrapper_src = crate_path / "src" / "wrapper.cpp"
    if not wrapper_src.exists():
        raise FileNotFoundError(f"Wrapper source not found: {wrapper_src}")
    source_files.append(wrapper_src)

    runtime_sources = [include_dir / "verilated.cpp"]
    threads_src = include_dir / "verilated_threads.cpp"
    if threads_src.exists():
        runtime_sources.append(threads_src)
    dpi_src = include_dir / "verilated_dpi.cpp"
    if dpi_src.exists():
        runtime_sources.append(dpi_src)
    source_files.extend(runtime_sources)

    compiler = _compiler_command()
    compile_cmd = compiler + [
        "-std=c++17",
        "-O3",
        "-fPIC",
        "-Wno-unused-parameter",
        "-I",
        str(obj_dir),
        "-I",
        str(include_dir),
        "-I",
        str(vltstd_dir),
    ]

    lib_filename = f"lib{crate.dynamic_lib_name}{_dynamic_lib_suffix()}"
    lib_path = crate_path / lib_filename
    if lib_path.exists():
        lib_path.unlink()

    system = platform.system().lower()
    if system == "darwin":
        compile_cmd.extend(["-dynamiclib", "-o", str(lib_path)])
        compile_cmd.extend(["-Wl,-install_name,@rpath/" + lib_filename])
    else:
        compile_cmd.extend(["-shared", "-o", str(lib_path)])

    compile_cmd.extend(str(path) for path in source_files)

    if system == "darwin":
        compile_cmd.append("-lc++")
    elif system != "windows":
        compile_cmd.append("-lstdc++")

    _run_subprocess(compile_cmd)

    if not lib_path.exists():
        raise RuntimeError(f"Failed to build Verilator shared library at {lib_path}")

    manifest_path = crate_path / ".verilator-lib-path"
    manifest_path.write_text(str(lib_path.resolve()), encoding="utf-8")

    crate.lib_filename = lib_filename
    crate.lib_path = lib_path.resolve()
    return crate.lib_path


def generate_external_sv_crates(
    modules: Iterable[ExternalSV],
    simulator_root: Path,
    verilator_root: Path,
) -> List[ExternalFFIModule]:
    """Generate Verilator FFI crates for the provided ExternalSV modules."""

    # pylint: disable=too-many-locals, too-many-statements

    specs: List[ExternalFFIModule] = []
    used_crate_names: Dict[str, int] = {}
    used_dynlib_names: Dict[str, int] = {}

    shutil.rmtree(verilator_root, ignore_errors=True)
    verilator_root.mkdir(parents=True, exist_ok=True)

    for module in modules:
        if not getattr(module, 'file_path', None):
            continue
        top_module = module.external_module_name
        if not top_module:
            raise ValueError("ExternalSV module must specify 'module_name' to drive Verilator")

        base_name = namify(top_module) or namify(module.name)
        if not base_name:
            base_name = "external"  # fallback
        if base_name[0].isdigit():
            base_name = f"ext_{base_name}"

        crate_name = f"verilated_{base_name}"
        count = used_crate_names.get(crate_name, 0)
        if count:
            crate_name = f"{crate_name}_{count+1}"
        used_crate_names[crate_name] = count + 1

        symbol_prefix = crate_name
        dynlib_base = f"{symbol_prefix}_ffi"
        dyn_count = used_dynlib_names.get(dynlib_base, 0)
        dynamic_lib_name = dynlib_base if dyn_count == 0 else f"{dynlib_base}_{dyn_count+1}"
        used_dynlib_names[dynlib_base] = dyn_count + 1

        crate_path = verilator_root / crate_name
        crate_path.mkdir(parents=True, exist_ok=True)
        (crate_path / "src").mkdir(exist_ok=True)
        (crate_path / "rtl").mkdir(exist_ok=True)

        src_sv_path = _ensure_repo_local_path(module.file_path)
        if not src_sv_path.exists():
            raise FileNotFoundError(f"ExternalSV file not found: {src_sv_path}")
        dst_sv_path = crate_path / "rtl" / src_sv_path.name
        shutil.copy(src_sv_path, dst_sv_path)

        ports_in = []
        ports_out = []
        for name, wire in module.wires.items():
            port = _dtype_to_port(name, wire)
            if port.direction == 'output':
                ports_out.append(port)
            else:
                ports_in.append(port)

        spec = ExternalFFIModule(
            crate_name=crate_name,
            crate_path=crate_path,
            symbol_prefix=namify(symbol_prefix),
            dynamic_lib_name=namify(dynamic_lib_name),
            top_module=top_module,
            sv_filename=src_sv_path.name,
            sv_rel_path=os.path.join('rtl', src_sv_path.name),
            inputs=ports_in,
            outputs=ports_out,
            has_clock=getattr(module, 'has_clock', False),
            has_reset=getattr(module, 'has_reset', False),
            original_module_name=module.name,
        )

        cargo_toml = _generate_cargo_toml(spec)
        lib_rs = _generate_lib_rs(spec)
        wrapper_cpp = _generate_wrapper_cpp(spec)

        _write_file(crate_path / "Cargo.toml", cargo_toml)
        _write_file(crate_path / "src/lib.rs", lib_rs)
        _write_file(crate_path / "src/wrapper.cpp", wrapper_cpp)

        _build_verilator_library(spec)
        specs.append(spec)

    manifest = {
        "modules": [
            {
                "crate": spec.crate_name,
                "dynamic_lib": spec.dynamic_lib_name,
                "top_module": spec.top_module,
                "sv": spec.sv_filename,
                "crate_dir": os.path.relpath(spec.crate_path, simulator_root),
                "struct_name": spec.struct_name,
                "has_clock": spec.has_clock,
                "has_reset": spec.has_reset,
                "lib_filename": spec.lib_filename,
                "lib_path": str(spec.lib_path) if spec.lib_path else "",
                "inputs": [
                    {
                        "name": port.name,
                        "bits": port.bits,
                        "signed": port.signed,
                        "rust_type": port.rust_type,
                        "c_type": port.c_type,
                    }
                    for port in spec.inputs
                ],
                "outputs": [
                    {
                        "name": port.name,
                        "bits": port.bits,
                        "signed": port.signed,
                        "rust_type": port.rust_type,
                        "c_type": port.c_type,
                    }
                    for port in spec.outputs
                ],
                "original_module_name": spec.original_module_name,
            }
            for spec in specs
        ]
    }

    if specs:
        manifest_path = simulator_root / "external_modules.json"
        _write_file(manifest_path, json.dumps(manifest, indent=2))

    return specs
