# Call Expression Generation

This module provides Verilog code generation for call operations, including async calls, wire assignments, and wire reads, with special handling for external SystemVerilog modules and the bookkeeping needed to wire their ports into the generated PyCDE design.

## Summary

The call expression generation module handles the conversion of Assassyn call operations into Verilog code. It manages async calls for the credit-based pipeline architecture, wire operations for inter-module communication, and provides special support for external SystemVerilog modules.

## Exposed Interfaces

### `codegen_async_call`

```python
def codegen_async_call(dumper, expr: AsyncCall) -> Optional[str]:
    """Generate code for async call operations."""
```

**Explanation**

This function handles async call operations by registering them with the module's expose mechanism. Async calls are fundamental to the credit-based pipeline architecture described in [arch.md](/docs/design/arch/arch.md).

The function performs the following steps:

1. **Expose Registration**: Calls `dumper.expose('trigger', expr)` to register the async call operation
2. **Deferred Processing**: The actual trigger logic is generated later during the cleanup phase

The cleanup phase handles async call operations by:
- Summing all trigger predicates for each target module
- Generating trigger signals that increment the target module's credit counter
- Creating proper credit-based flow control signals

This deferred approach allows the cleanup phase to:
- Group multiple async calls to the same module
- Generate appropriate trigger signal multiplexing
- Handle credit counter management

**Project-specific Knowledge Required**:
- Understanding of [async call operations](/python/assassyn/ir/expr/call.md)
- Knowledge of [credit-based pipeline architecture](/docs/design/arch/arch.md)
- Understanding of [expose mechanism](/python/assassyn/codegen/verilog/design.md)
- Reference to [cleanup phase processing](/python/assassyn/codegen/verilog/cleanup.md)

### `codegen_bind`

```python
def codegen_bind(_dumper, _expr: Bind) -> Optional[str]:
    """Generate code for bind operations.

    Bind operations don't generate any code, they just represent bindings.
    """
```

**Explanation**

This function handles bind operations, which represent parameter bindings in function calls. Bind operations don't generate any Verilog code directly as they are purely structural elements that organize the call parameters.

The function is intentionally empty because:
- Bind operations are processed during the async call generation
- They don't represent actual hardware operations
- The binding information is used by the async call handler to generate proper parameter connections

**Project-specific Knowledge Required**:
- Understanding of [bind operations](/python/assassyn/ir/expr/call.md)
- Knowledge of [parameter binding in calls](/python/assassyn/ir/expr/call.md)

### `codegen_wire_assign`

```python
def codegen_wire_assign(dumper, expr: WireAssign) -> Optional[str]:
    """Generate code for wire assign operations."""
```

**Explanation**

This function handles wire assignment operations, with special handling for external SystemVerilog modules. It performs the following steps:

1. **External Module Detection**: Checks if the wire assignment targets a port exposed by an `ExternalSV` module.
2. **Input Registration**: For external modules, records the `<wire_name, value>` pair inside `dumper.pending_external_inputs[owner]`. These queued inputs are consumed when the external instance is emitted so all connections are applied in one place.
3. **Comment Generation**: Returns a comment documenting the assignment, which helps the generator track where external connections originated.

Non-external wire assignments simply leave a breadcrumb comment, while external assignments queue up the data so `codegen_wire_read` can wire the signals when the external module is instantiated.

**Project-specific Knowledge Required**:
- Understanding of [wire assignment operations](/python/assassyn/ir/expr/call.md)
- Knowledge of [external SystemVerilog modules](/python/assassyn/ir/module/external.md)
- Understanding of [wire operations](/python/assassyn/ir/module/base.md)

### `codegen_wire_read`

```python
def codegen_wire_read(dumper, expr: WireRead) -> Optional[str]:
    """Generate code for wire read operations."""
```

**Explanation**

This function generates Verilog code for wire read operations, with comprehensive handling for external SystemVerilog modules. It performs the following steps:

1. **Comment Generation**: Emits a `# External wire read` comment to keep the generated script traceable.
2. **External Registration**: Delegates to `register_external_wire_read` so the dumper can record provenance. That helper determines whether the read happens within the producer module or remotely, and tracks the wiring needed to move the value across module boundaries.
3. **External Module Instantiation**: If the current module is the external producer, ensures the external wrapper is instantiated exactly once. Pending inputs queued by `codegen_wire_assign` are connected at instantiation time, along with optional clock/reset ports.
4. **Wire Access**: Returns the appropriate assignment or expression depending on whether the read is local, remote, or unresolved. When reading across modules the helper generates `self.<port_name>` loads; when reading inside the producer it references the instantiated external wrapper (`<ext>_ffi_inst.<wire_name>`).

If the requested external output is already mapped to a local simulator field, the function suppresses redundant assignments to avoid generating self-assignments.

**Project-specific Knowledge Required**:
- Understanding of [wire read operations](/python/assassyn/ir/expr/call.md)
- Knowledge of [external SystemVerilog modules](/python/assassyn/ir/module/external.md)
- Understanding of [module instantiation](/python/assassyn/codegen/verilog/module.md)
- Reference to [right-hand value generation](/python/assassyn/codegen/verilog/rval.md)

## Internal Helpers

### `register_external_wire_read`

```python
def register_external_wire_read(dumper, expr: WireRead):
    """Register bookkeeping for external wire reads without emitting code."""
```

**Explanation**

This helper records the relationships uncovered during an external wire read:

- Registers the expression with `dumper.expose('expr', expr)` so the producer can expose the value if needed.
- When the read occurs in a different module, records the consumer/producer pair plus wiring metadata (`external_wire_assignments`). The top-level harness later consumes this list to insert cross-module wires and assignments.
- When the read happens inside the external producer, notes the output name in `dumper.external_wire_outputs` so downstream consumers know which expose port to bind.

By keeping bookkeeping separate from code emission, the generator can assemble external wiring once during the cleanup stages, ensuring consistent connections across downstream modules and the top-level harness.

---

The module also relies on:

- `dump_rval()` from [rval module](/python/assassyn/codegen/verilog/rval.md) for generating signal references
- `namify()` from [utils module](/python/assassyn/utils.md) for name generation

The call expression generation is integrated into the main expression dispatch system through the [__init__.py](/python/assassyn/codegen/verilog/_expr/__init__.md) module, which routes different expression types to their appropriate code generation functions.
