"""Helpers for generating Verilator FFI crates for ExternalSV modules."""

from __future__ import annotations

import json
import os
import shutil
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List

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
class ExternalFFIModule:
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


def _generate_cargo_toml(crate: ExternalFFIModule) -> str:
    return f"""[package]
name = \"{crate.crate_name}\"
version = \"0.1.0\"
edition = \"2021\"
links = \"{crate.dynamic_lib_name}\"

[dependencies]

[build-dependencies]
cc = \"1\"
"""


def _generate_build_rs(crate: ExternalFFIModule) -> str:
    top_module = crate.top_module
    cpp_class = f"V{top_module}"
    sv_rel_path = crate.sv_rel_path.replace('\\', '/')
    dynlib = crate.dynamic_lib_name
    aggregated = f"{cpp_class}__ALL.cpp"
    template = """use std::{env, fs, path::PathBuf, process::Command};

fn dynamiclib_suffix() -> &'static str {
    match env::var("CARGO_CFG_TARGET_OS").unwrap_or_default().as_str() {
        "windows" => ".dll",
        "macos" => ".dylib",
        _ => ".so",
    }
}

fn main() {
    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let sv_path = manifest_dir.join("__SV_PATH__");
    let out_dir = PathBuf::from(env::var("OUT_DIR").unwrap());
    let obj_dir = out_dir.join("verilated");
    fs::create_dir_all(&obj_dir).unwrap();

    let verilator_exe = env::var("ASSASSYN_VERILATOR").unwrap_or_else(|_| "verilator".to_string());
    let status = Command::new(&verilator_exe)
        .arg("--cc")
        .arg(&sv_path)
        .arg("--top-module")
        .arg("__TOP_MODULE__")
        .arg("-O3")
        .arg("--Mdir")
        .arg(&obj_dir)
        .status()
        .expect("failed to run verilator");
    if !status.success() {
        panic!("verilator failed with status {}", status);
    }

    let verilator_root = PathBuf::from(
        env::var("VERILATOR_ROOT").expect("VERILATOR_ROOT is not set. Did you source setup.sh?")
    );
    let include_dir = verilator_root.join("include");
    let vltstd_dir = include_dir.join("vltstd");

    let mut model_build = cc::Build::new();
    model_build
        .cpp(true)
        .flag_if_supported("-std=c++17")
        .flag_if_supported("-Wno-unused-parameter")
        .flag_if_supported("-fPIC")
        .include(&obj_dir)
        .include(&include_dir)
        .include(&vltstd_dir);
    let aggregated = obj_dir.join("__AGGREGATED__");
    if aggregated.exists() {
        model_build.file(&aggregated);
    } else {
        for entry in fs::read_dir(&obj_dir).expect("read verilator output") {
            let path = entry.expect("dir entry").path();
            if path.extension().and_then(|e| e.to_str()) == Some("cpp") {
                if path
                    .file_name()
                    .and_then(|name| name.to_str())
                    .map(|name| name.ends_with("__ALL.cpp"))
                    .unwrap_or(false)
                {
                    continue;
                }
                model_build.file(path);
            }
        }
    }
    for runtime in ["verilated.cpp", "verilated_threads.cpp"] {
        let candidate = include_dir.join(runtime);
        if candidate.exists() {
            model_build.file(candidate);
        }
    }
    model_build.compile("verilated_model");

    let wrapper_src = manifest_dir.join("src/wrapper.cpp");

    let mut build = cc::Build::new();
    build
        .cpp(true)
        .file(&wrapper_src)
        .include(&obj_dir)
        .include(&include_dir)
        .include(&vltstd_dir)
        .flag_if_supported("-std=c++17")
        .flag_if_supported("-Wno-unused-parameter")
        .flag_if_supported("-fPIC");
    let compiler = build.get_compiler();
    let compiler_path = compiler.path().to_path_buf();
    if compiler.is_like_msvc() {
        panic!("MSVC toolchain is not supported for Verilator FFI builds");
    }
    build.compile("ffi_wrapper");

    let wrapper_static = out_dir.join("libffi_wrapper.a");
    let model_static = out_dir.join("libverilated_model.a");

    let dynlib_name = format!("lib__DYNLIB__{}", dynamiclib_suffix());
    let dynlib_path = out_dir.join(&dynlib_name);

    let target_os = env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    let target_env = env::var("CARGO_CFG_TARGET_ENV").unwrap_or_default();

    let mut linker = Command::new(compiler_path);
    if target_os == "macos" {
        if let Ok(deployment_target) = env::var("MACOSX_DEPLOYMENT_TARGET") {
            linker.env("MACOSX_DEPLOYMENT_TARGET", deployment_target);
        }
        linker.arg("-dynamiclib");
        linker.arg("-o");
        linker.arg(&dynlib_path);
        linker.arg(format!("-Wl,-install_name,@rpath/{}", dynlib_name));
        for lib in [&wrapper_static, &model_static] {
            linker.arg(format!("-Wl,-force_load,{}", lib.display()));
        }
        linker.arg("-lc++");
    } else {
        linker.arg("-shared");
        linker.arg("-o");
        linker.arg(&dynlib_path);
        linker.arg("-Wl,--whole-archive");
        linker.arg(&wrapper_static);
        linker.arg(&model_static);
        linker.arg("-Wl,--no-whole-archive");
        if target_env != "msvc" {
            linker.arg("-lstdc++");
        }
    }

    let status = linker.status().expect("failed to link shared library");
    if !status.success() {
        panic!("linker failed with status {}", status);
    }

    println!("cargo:rustc-link-search=native={}", out_dir.display());
    if target_os != "windows" {
        println!("cargo:rustc-link-arg=-Wl,-rpath,{}", out_dir.display());
    }
    println!("cargo:rustc-link-lib=dylib=__DYNLIB__");
    if target_os == "macos" {
        println!("cargo:rustc-link-lib=c++");
    } else if target_env != "msvc" {
        println!("cargo:rustc-link-lib=stdc++");
    }
    println!("cargo:rerun-if-changed=__SV_PATH__");
    println!("cargo:rerun-if-changed=src/wrapper.cpp");
    println!("cargo:rerun-if-env-changed=VERILATOR_ROOT");
    println!("cargo:rerun-if-env-changed=ASSASSYN_VERILATOR");
}
"""
    return textwrap.dedent(template).replace("__SV_PATH__", sv_rel_path).replace("__TOP_MODULE__", top_module).replace("__AGGREGATED__", aggregated).replace("__DYNLIB__", dynlib)



def _generate_lib_rs(crate: ExternalFFIModule) -> str:
    struct_name = camelize(crate.symbol_prefix) or "ExternalModule"
    struct_name = struct_name[0].upper() + struct_name[1:]
    crate.struct_name = struct_name
    raw_mod_lines = ["pub mod raw {", "    use super::ModuleHandle;"]
    prefix = crate.symbol_prefix
    raw_mod_lines.extend([
        "    extern \"C\" {",
        f"        pub fn {prefix}_new() -> *mut ModuleHandle;",
        f"        pub fn {prefix}_free(handle: *mut ModuleHandle);",
        f"        pub fn {prefix}_eval(handle: *mut ModuleHandle);",
    ])
    if crate.has_clock:
        raw_mod_lines.append(
            f"        pub fn {prefix}_set_clk(handle: *mut ModuleHandle, value: u8);"
        )
    if crate.has_reset:
        raw_mod_lines.append(
            f"        pub fn {prefix}_set_rst(handle: *mut ModuleHandle, value: u8);"
        )
    for port in crate.inputs:
        raw_mod_lines.append(
            f"        pub fn {prefix}_set_{port.name}(handle: *mut ModuleHandle, value: {port.rust_type});"
        )
    for port in crate.outputs:
        raw_mod_lines.append(
            f"        pub fn {prefix}_get_{port.name}(handle: *mut ModuleHandle) -> {port.rust_type};"
        )
    raw_mod_lines.append("    }")
    raw_mod_lines.append("}")

    struct_lines = [
        "#[allow(dead_code)]",
        f"pub struct {struct_name} {{",
        "    ptr: *mut ModuleHandle,",
    ]
    if crate.has_clock:
        struct_lines.append("    clk_state: u8,")
    if crate.has_reset:
        struct_lines.append("    rst_state: u8,")
    struct_lines.append("}")
    struct_lines.append("")

    impl_lines = [
        f"impl {struct_name} {{",
        "    pub fn new() -> Self {",
        f"        let ptr = unsafe {{ raw::{prefix}_new() }};",
        f"        assert!(!ptr.is_null(), \"{prefix}_new returned null\");",
    ]
    if crate.has_clock:
        impl_lines.append(f"        unsafe {{ raw::{prefix}_set_clk(ptr, 0); }}")
    if crate.has_reset:
        impl_lines.append(f"        unsafe {{ raw::{prefix}_set_rst(ptr, 0); }}")
    impl_lines.append("        Self {")
    impl_lines.append("            ptr,")
    if crate.has_clock:
        impl_lines.append("            clk_state: 0,")
    if crate.has_reset:
        impl_lines.append("            rst_state: 0,")
    impl_lines.append("        }")
    impl_lines.append("    }")
    impl_lines.append("")
    impl_lines.append(
        f"    pub fn eval(&mut self) {{ unsafe {{ raw::{prefix}_eval(self.ptr) }} }}"
    )
    if crate.has_clock or crate.has_reset:
        impl_lines.append("")
    if crate.has_clock:
        impl_lines.extend(
            [
                "    pub fn set_clock(&mut self, value: bool) {",
                "        let value = value as u8;",
                f"        unsafe {{ raw::{prefix}_set_clk(self.ptr, value) }};",
                "        self.clk_state = value;",
                "    }",
                "",
            ]
        )
    if crate.has_reset:
        impl_lines.extend(
            [
                "    pub fn set_reset(&mut self, value: bool) {",
                "        let value = value as u8;",
                f"        unsafe {{ raw::{prefix}_set_rst(self.ptr, value) }};",
                "        self.rst_state = value;",
                "    }",
                "",
            ]
        )
    if crate.has_clock:
        impl_lines.extend(
            [
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
        impl_lines.append(
            f"    pub fn set_{port.name}(&mut self, value: {port.rust_type}) {{ unsafe {{ raw::{prefix}_set_{port.name}(self.ptr, value) }} }}"
        )
    for port in crate.outputs:
        impl_lines.append(
            f"    pub fn get_{port.name}(&mut self) -> {port.rust_type} {{ unsafe {{ raw::{prefix}_get_{port.name}(self.ptr) }} }}"
        )
    impl_lines.append("}")
    impl_lines.append("")

    drop_lines = [
        f"impl Drop for {struct_name} {{",
        f"    fn drop(&mut self) {{ unsafe {{ raw::{prefix}_free(self.ptr) }} }}",
        "}",
    ]

    return "\n".join([
        "#![allow(dead_code)]",
        "#[repr(C)]",
        "#[allow(non_camel_case_types)]",
        "pub struct ModuleHandle { _private: [u8; 0] }",
        "",
        *raw_mod_lines,
        "",
        *struct_lines,
        *impl_lines,
        *drop_lines,
    ])


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
        lines.append(
            f"void {prefix}_set_clk(ModuleHandle* handle, uint8_t value) {{ handle->clk = static_cast<uint8_t>(value & 0x1U); }}"
        )
    if crate.has_reset:
        lines.append(
            f"void {prefix}_set_rst(ModuleHandle* handle, uint8_t value) {{ handle->rst = static_cast<uint8_t>(value & 0x1U); }}"
        )
    for port in crate.inputs:
        lines.append(
            f"void {prefix}_set_{port.name}(ModuleHandle* handle, {port.c_type} value) {{ handle->{port.name} = static_cast<{port.c_type}>(value); }}"
        )
    for port in crate.outputs:
        lines.append(
            f"{port.c_type} {prefix}_get_{port.name}(ModuleHandle* handle) {{ return static_cast<{port.c_type}>(handle->{port.name}); }}"
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def generate_external_sv_crates(
    modules: Iterable[ExternalSV],
    simulator_root: Path,
    verilator_root: Path,
) -> List[ExternalFFIModule]:
    """Generate Verilator FFI crates for the provided ExternalSV modules."""

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
        build_rs = _generate_build_rs(spec)
        lib_rs = _generate_lib_rs(spec)
        wrapper_cpp = _generate_wrapper_cpp(spec)

        _write_file(crate_path / "Cargo.toml", cargo_toml)
        _write_file(crate_path / "build.rs", build_rs)
        _write_file(crate_path / "src/lib.rs", lib_rs)
        _write_file(crate_path / "src/wrapper.cpp", wrapper_cpp)

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
