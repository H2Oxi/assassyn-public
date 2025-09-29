# ExternalSV Support in the Simulator

This document explains how the simulator generator integrates Verilog modules that are provided through the `ExternalSV` interface. It covers how the code generator discovers these modules, what Rust and C++ glue files are emitted, and how the runtime interacts with the generated bindings during simulation.

## Overview

`ExternalSV` modules represent design blocks that live in standalone SystemVerilog sources instead of the core Assassyn IR. During simulator generation we build a Verilator-based foreign-function interface (FFI) crate for each external module and link those crates into the Rust simulator. Each crate exposes a safe Rust wrapper (`struct` with `set_*`/`get_*` helpers) while the simulator runtime handles scheduling of `eval`/`clock_tick` calls and data movement between the Rust world and the verilated model.

## Generation Workflow

1. **Discovery** – When `elaborate()` runs, `external.generate_external_sv_crates()` scans the system for `ExternalSV` modules. If none are present the Verilator workspace is skipped entirely.
2. **Workspace preparation** – The Verilator output directory is cleared and recreated. A dedicated Cargo crate directory is created for every module (`verilated_<module>`), including `src/` and `rtl/` subdirectories.
3. **Source staging** – The original SystemVerilog file referenced by `ExternalSV.file_path` is copied into the crate’s `rtl/` directory.
4. **Metadata collection** – Port directions, bit widths, and signedness are mapped to both C and Rust scalar types (currently up to 64 bits). Clock and reset presence is recorded.
5. **Code emission** – Four artifacts are emitted per crate:
   - `Cargo.toml` declaring the crate and the `cc` build dependency.
   - `build.rs` (from `build_rs_template.rs`) which drives Verilator and links the generated model into a shared library.
   - `src/lib.rs` exposing a safe Rust wrapper around the raw C FFI.
   - `src/wrapper.cpp` implementing the C ABI that bridges to the verilated C++ model.
6. **Manifest** – A `external_modules.json` file is written beside the simulator Cargo project summarising every external module (crate path, library name, struct wrapper, ports, clocks, resets). The simulator generator later consumes this manifest to wire handles and link crates.

### Naming strategy

Crate names default to `verilated_<external_module_name>` (sanitised with `namify`). Dynamic library names follow `<crate>_ffi`. Numeric suffixes are added automatically to avoid collisions when multiple external modules resolve to the same basename.

## Generated Crate Details

### `build.rs`

The build script wraps Verilator invocation. It:
- Picks the Verilator executable from `ASSASSYN_VERILATOR` or falls back to `verilator` on `PATH`.
- Requires `VERILATOR_ROOT` to locate headers and runtimes.
- Places C++ output under `$OUT_DIR/verilated`, compiles it using `cc::Build`, and links `verilated.cpp`/`verilated_threads.cpp` when available.
- Builds `src/wrapper.cpp`, then links the generated static archives into a shared library whose suffix matches the target OS (`.so`, `.dylib`, `.dll`).
- Emits appropriate `cargo:rustc-*` directives so the simulator links against the shared library and refreshes when SV sources or environment variables change.

### `src/wrapper.cpp`

The wrapper exposes a C ABI tailored to the module’s ports:
- `*_new`, `*_free`, and `*_eval` manage the verilated instance lifetime.
- Optional `*_set_clk` / `*_set_rst` appear for clock or reset ports.
- For each input port: `*_set_<port>(ModuleHandle*, <ctype>)` performs a narrow cast and assigns into the verilated instance.
- For each output port: `*_get_<port>(ModuleHandle*)` returns the port value.
- Types are generated as the closest matching C integer (`uint{8,16,32,64}_t` or signed equivalents).

### `src/lib.rs`

`lib.rs` wraps the C signatures in a Rust `struct` with safe-ish semantics:
- The raw functions live under a `pub mod raw` exposing `extern "C"` signatures.
- The wrapper tracks an owned `*mut ModuleHandle`; `Drop` automatically calls `*_free`.
- Inputs map to `set_<port>` methods consuming Rust scalar types (`u{8,16,32,64}` or signed variants). Outputs use `get_<port>`.
- When clocks or resets exist we expose `set_clock`, `clock_tick`, `set_reset`, and `apply_reset`. Clock ticks toggle the clock low/high with interleaved `eval()` calls.
- The `struct` name is derived from the symbol prefix (CamelCase). This name is used by the simulator to reference the handle field (`<module>_ffi`).

## Simulator Integration

`modules.py` and related generator pieces consume the manifest data to bind external crates into the Rust simulator:
- The simulator `Cargo.toml` adds path dependencies on every generated crate so that Cargo builds them alongside the main binary.
- Each external module handle is stored on the simulator struct using `external_handle_field(<module>)`, naming it `<module>_ffi`.
- When a wire assignment targets an external module input the generated Rust code:
  ```rust
  sim.<module>_ffi.set_<port>(ValueCastTo::<Ty>::cast(&value));
  ```
  The generator tracks which external modules received input updates during the current downstream invocation.
- For wire reads from external outputs the generator emits a block that (for combinational modules) calls `eval()` before fetching the value to keep inputs and outputs in sync.
- Downstream modules that interact with clocked external modules append `sim.<module>_ffi.clock_tick();` at the end of their body so the verilated design advances once per downstream evaluation.
- Inline comments (`// External wire assign`, `/* External wire read */`) are included to make the generated Rust easier to audit.

## Runtime Expectations

- External modules without clocks are treated as combinational blocks: inputs are driven via `set_*` calls and `eval()` is automatically inserted immediately before an output read if inputs changed in the same downstream visit.
- External modules with clocks rely on periodic `clock_tick()` calls from their owning downstream module. The simulator toggles the clock and performs two `eval()` calls (falling then rising edge). Resets, when present, can be asserted via `set_reset`/`apply_reset`.
- The generated manifest allows higher-level tooling to load external shared libraries at runtime if dynamic loading is required (the simulator links statically by default through the path dependencies).

## Environment Requirements and Customisation

- Install Verilator and ensure `VERILATOR_ROOT` points at the installation root (the generator fails early if the variable is missing).
- Optional `ASSASSYN_VERILATOR` lets you override the Verilator binary location.
- The generated crates use `cc` for compilation, so a C++17-capable toolchain must be available on the host system.
- If you need to tweak the build flags or linking strategy, modify `python/assassyn/codegen/simulator/build_rs_template.rs`. Because `_generate_build_rs` simply performs placeholder substitution, custom changes in the template propagate to all future crate generations.

## Debugging Tips

- Inspect `<simulator>/external_modules.json` to confirm the generator recognised the expected ports, clock/reset flags, and crate layout.
- The generated crates live under `<workspace>/<verilator_dirname>/`. Running `cargo build` inside the simulator project rebuilds all external FFIs and provides compiler errors with direct source references.
- To validate behaviour, you can instantiate the Rust wrappers manually from a standalone test harness and call the exposed methods (`set_*`, `get_*`, `clock_tick`) to mimic what the simulator does.

This pipeline ensures `ExternalSV` modules compile into verilated models that integrate seamlessly with the Rust simulator, with a clear separation between generated glue code and template-driven build logic.
