# External Module Utilities

Helper functions in `external.py` provide the simulator generator with the metadata it needs to wire `ExternalSV` blocks into the Rust runtime. The utilities focus on discovering value dependencies, collecting wire assignments, and producing manifest data for Verilator crates.

## Section 0. Summary

During simulator generation we analyse the elaborated IR to determine what values must be cached, which external modules appear as pure stubs, and which Rust handles should be created. The APIs in this module centralise those analyses so other codegen passes (for example `modules.py` and `verilator.py`) can reuse the same bookkeeping.

**Recent Refactoring (2025-01):**
This module has been refactored to:
1. Better utilize `Operand.__getattr__` forwarding to reduce explicit `unwrap_operand()` calls
2. Consolidate common patterns like wire owner lookup into helper functions (`_get_wire_owner`)
3. Simplify `Visitor` subclasses to directly inherit traversal logic from the parent class
4. Make functions more tolerant of `Operand` wrappers, unwrapping only when necessary for type checking

These changes improve code clarity and reduce coupling while maintaining full backward compatibility.

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

### `codegen_external_wire_assign`

```python
def codegen_external_wire_assign(
    node: WireAssign,
    *,
    external_specs: Dict[str, Any],
    external_value_assignments: Dict[tuple, List[Tuple[ExternalSV, Wire]]],
    value_code: str,
) -> str | None:
```

Produces Rust simulator code for driving an `ExternalSV` input wire. Returns `None` if the wire is not external, empty string if handled by producer, or the generated assignment code.

**Parameters:**
- `node`: The `WireAssign` node to generate code for
- `external_specs`: FFI specifications from Verilator pass
- `external_value_assignments`: Mapping of producer expressions to external consumers
- `value_code`: Pre-generated Rust code for the assigned value

**Returns:**
- `None` if not an external wire assignment
- `""` if assignment is handled by the producer module
- Generated Rust code string for the FFI setter call

**Implementation Note:**
This function passes `node.value` directly to `_assignment_handled_by_producer` without unwrapping, demonstrating trust in that function's ability to handle `Operand` wrappers transparently.

### `codegen_external_wire_read`

```python
def codegen_external_wire_read(
    node: WireRead,
    *,
    external_specs: Dict[str, Any],
) -> str | None:
```

Produces Rust simulator code for reading from an `ExternalSV` output wire. Generates code that calls the FFI `eval()` and `get_*()` methods on the external module handle.

**Parameters:**
- `node`: The `WireRead` node to generate code for
- `external_specs`: FFI specifications from Verilator pass

**Returns:**
- `None` if not an external wire read
- Generated Rust code block including `eval()` call and value retrieval

### `has_module_body` / `is_stub_external`

```python
def has_module_body(module: Module) -> bool:
def is_stub_external(module: Module) -> bool:
```

Helpers that distinguish fully elaborated modules from placeholder stubs. Downstream passes use them to decide whether an `ExternalSV` can be ignored during Rust code emission.

The module also re-exports `iter_wire_assignments`, `collect_external_wire_reads`, and related helpers via `__all__` to keep imports concise.

## Section 2. Internal Helpers

### `_get_wire_owner`

```python
def _get_wire_owner(wire) -> Module | None:
```

Returns the owner module of a wire by checking both `parent` and `module` attributes. This helper consolidates the common pattern of `getattr(wire, "parent", None) or getattr(wire, "module", None)` into a single reusable function.

**Parameters:**
- `wire`: A `Wire` object to find the owner for

**Returns:**
- The owning module or None if not found

### `_assignment_handled_by_producer`

```python
def _assignment_handled_by_producer(
    value_expr: object,
    external_value_assignments: Dict[tuple, List[Tuple[ExternalSV, Wire]]],
) -> bool:
```

Determines if a value assignment to an external wire is already handled by the producer module. This function accepts either an `Expr` or an `Operand` wrapping an `Expr`, utilizing `Operand.__getattr__` to transparently access attributes without explicit unwrapping (except for type checking).

**Parameters:**
- `value_expr`: Expression or Operand to check (can be wrapped in Operand)
- `external_value_assignments`: Mapping of (module, value_id) to list of external wire consumers

**Returns:**
- True if the producer module will emit the assignment, False otherwise

**Implementation Note:**
This function demonstrates efficient use of `Operand.__getattr__` forwarding:
- Unwraps once for type checking (`isinstance`)
- Uses original `value_expr` for attribute access (e.g., `.parent`, `.as_operand()`)
- `__getattr__` automatically forwards these to the wrapped value

### `_ExternalWireReadCollector`

A `Visitor` subclass that collects `WireRead` expressions observing `ExternalSV` outputs. Simplified in recent refactoring to directly inherit from `Visitor` and override only `visit_expr`, leveraging the parent class's block traversal logic.

### `_ModuleValueExposureCollector`

A `Visitor` subclass that collects expressions requiring simulator-side caching. Uses `expr_externally_used` analysis and directly inherits `Visitor`'s traversal mechanisms.

### `iter_wire_assignments`

```python
def iter_wire_assignments(root: Block) -> Iterable[WireAssign]:
```

Depth-first iterator that yields every `WireAssign` inside a block hierarchy. This is how `collect_external_value_assignments` discovers which expressions drive an external input.

### Helper Pipeline

1. `collect_module_value_exposures` gathers values that escape the module through async calls, array writes, or other externally visible paths.
2. `collect_external_wire_reads` adds explicit output reads from `ExternalSV` modules.
3. `gather_expr_validities` merges the previous two sets, recording both the global exposure set and the owning module so the simulator can emit per-module caches and validity bits.
4. `collect_external_value_assignments` produces the reverse mapping—given an exposed value, which external modules consume it—so Rust glue can drive the correct FFI setters.

This flow ensures simulator code generation has the full picture of cross-module dataflow involving external SystemVerilog black boxes.
