# External Module Utilities

Helper functions in `external.py` provide the simulator generator with the metadata it needs to wire `ExternalSV` blocks into the Rust runtime. The utilities focus on discovering value dependencies, collecting wire assignments, and producing manifest data for Verilator crates.

## Section 0. Summary

During simulator generation we analyse the elaborated IR to determine what values must be cached, which external modules appear as pure stubs, and which Rust handles should be created. The APIs in this module centralise those analyses so other codegen passes (for example `modules.py` and `verilator.py`) can reuse the same bookkeeping.

## Section 1. Exposed Interfaces

### `external_handle_field`

```python
def external_handle_field(module_name: str) -> str:
```

Returns the field name used on the simulator struct to store the FFI handle for a specific `ExternalSV` module. The result is derived from `namify(module_name)` with a `_ffi` suffix.

### `collect_external_wire_reads`

```python
def collect_external_wire_reads(module: Module) -> Set[Expr]:
```

Walks a module body and records all `WireRead` expressions that observe outputs of an `ExternalSV`. These reads must trigger value exposure or Rust-side caching to keep combinational outputs coherent.

### `collect_module_value_exposures`

```python
def collect_module_value_exposures(module: Module) -> Set[Expr]:
```

Uses the `expr_externally_used` analysis to find expressions whose results are consumed outside the module. The returned set is merged with wire reads so the simulator knows which computed values must be stored on the shared context.

### `collect_external_value_assignments`

```python
def collect_external_value_assignments(sys) -> DefaultDict[tuple, List[Tuple[ExternalSV, Wire]]]:
```

Iterates over all `ExternalSV` downstream modules in the system and groups their input assignments by the IR expression that produces the driving value. The mapping is later used to emit Rust glue that forwards values into the appropriate FFI handle.

### `lookup_external_port`

```python
def lookup_external_port(external_specs, module_name: str, wire_name: str, direction: str):
```

Given the manifest dictionary emitted by the Verilator pass, returns the `FFIPort` entry that matches the requested module, wire, and direction. This keeps port-type lookups in one place.

### `gather_expr_validities`

```python
def gather_expr_validities(sys) -> Tuple[Set[Expr], Dict[Module, Set[Expr]]]:
```

Aggregates every expression that requires simulator-visible caching and produces both the global set and a per-module map. Callers use the result to create validity bits and optional value caches in the generated Rust code.

### `has_module_body` / `is_stub_external`

```python
def has_module_body(module: Module) -> bool:
def is_stub_external(module: Module) -> bool:
```

Helpers that distinguish fully elaborated modules from placeholder stubs. Downstream passes use them to decide whether an `ExternalSV` can be ignored during Rust code emission.

The module also re-exports `iter_wire_assignments`, `collect_external_wire_reads`, and related helpers via `__all__` to keep imports concise.

## Section 2. Internal Helpers

### `_walk_block`

Performs a shallow traversal of nested `Block` structures. The walker is reused by multiple collectors to avoid duplicating block iteration code.

### `iter_wire_assignments`

Depth-first iterator that yields every `WireAssign` inside a block hierarchy. This is how `collect_external_value_assignments` discovers which expressions drive an external input.

### Helper Pipeline

1. `collect_module_value_exposures` gathers values that escape the module through async calls, array writes, or other externally visible paths.
2. `collect_external_wire_reads` adds explicit output reads from `ExternalSV` modules.
3. `gather_expr_validities` merges the previous two sets, recording both the global exposure set and the owning module so the simulator can emit per-module caches and validity bits.
4. `collect_external_value_assignments` produces the reverse mapping—given an exposed value, which external modules consume it—so Rust glue can drive the correct FFI setters.

This flow ensures simulator code generation has the full picture of cross-module dataflow involving external SystemVerilog black boxes.
