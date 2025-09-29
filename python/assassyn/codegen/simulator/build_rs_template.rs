use std::{env, fs, path::PathBuf, process::Command};

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
