# Verilog Design Generation

This module provides the main Verilog design generation functionality, including the CIRCTDumper class that converts Assassyn IR into CIRCT-compatible Verilog code and the generate_design function that orchestrates the complete design generation process. The generator also accumulates the metadata required to stitch together external SystemVerilog modules and multi-port array writers.

## Summary

The design generation module is the core of the Verilog backend, responsible for converting Assassyn intermediate representation into synthesizable Verilog code. It implements the credit-based pipeline architecture through the CIRCTDumper class, which now handles module generation, array management, external module integration, trigger counter plumbing, and the complete design synthesis process.

## Exposed Interfaces

### `generate_design`

```python
def generate_design(fname: str, sys: SysBuilder):
    """Generate a complete Verilog design file for the system."""
```

**Explanation**

This function generates a complete Verilog design file for an Assassyn system. It performs the following steps:

1. **File Setup**: Opens the output file and writes the standard CIRCT header
2. **SRAM Module Generation**: Generates SRAM blackbox module definitions for each SRAM in the system
3. **System Processing**: Uses CIRCTDumper to visit and generate code for all modules in the system
4. **Code Output**: Writes the generated code to the file
5. **Log Return**: Returns the generated log statements for testbench integration

The function handles SRAM modules specially by:
- Extracting SRAM parameters (data width, address width, array name)
- Generating parameterized SRAM blackbox modules
- Creating proper memory interface definitions

**Project-specific Knowledge Required**:
- Understanding of [system builder](/python/assassyn/builder.md)
- Knowledge of [SRAM memory model](/python/assassyn/ir/memory/sram.md)
- Understanding of [CIRCTDumper integration](/python/assassyn/codegen/verilog/design.md)
- Reference to [Verilog elaboration process](/python/assassyn/codegen/verilog/elaborate.md)

## Internal Classes

### `CIRCTDumper`

```python
class CIRCTDumper(Visitor):
    """Dumps IR to CIRCT-compatible Verilog code."""
```

**Explanation**

The CIRCTDumper class is the main visitor that converts Assassyn IR into Verilog code. It inherits from the Visitor pattern and implements the credit-based pipeline architecture. The class maintains extensive state for managing:

1. **Execution Control**: `wait_until`, `cond_stack`, and `finish_conditions` track predicate stacking, wait-until clauses, and FINISH intrinsics.
2. **Module State**: `current_module`, `_exposes`, `module_ctx`, and `exposed_ports_to_add` capture which values need to become ports.
3. **Array Management**: `array_write_port_mapping`, `array_users`, `sram_payload_arrays`, and `memory_defs` orchestrate multi-port array writers and SRAM payloads.
4. **External Integration**: `pending_external_inputs`, `instantiated_external_modules`, `external_wire_assignments`, `external_wire_assignment_keys`, `external_wire_outputs`, and `external_modules` keep track of how external SystemVerilog modules are instantiated and how their signals flow from producers to consumers.
5. **Expression Naming**: `expr_to_name` and `name_counters` guarantee deterministic signal names whenever expression results must be reused across statements.
6. **Code Generation**: `code`, `logs`, and `indent` store emitted lines and diagnostic information used later by the testbench.

#### Key Methods

**`visit_system`**: Generates code for the entire system by calling `generate_system()`

**`visit_module`**: Generates a complete Verilog module with the following phases:
1. **Analysis Phase**: Processes the module body, collecting exposes, async call metadata, and external wiring information.
2. **Port Generation**: Calls `generate_module_ports()` to create module interfaces, using the captured expose data.
3. **Code Integration**: Combines the collected body statements with the module boilerplate and generator decorators.
4. **Special Handling**: Resets external bookkeeping between modules, emits SRAM-specific prelude code, and avoids instantiating pure external stubs.

**`visit_array`**: Generates multi-port array modules with:
- Write port interfaces for each writing module (based on `array_write_port_mapping`)
- Multi-port arbitration loops that prioritise the last matching port
- Register-based storage with programmable reset values
- Outputs that feed downstream module array readers

**`visit_expr`**: Delegates expression generation to the expression dispatch system, emits helpful `#` comments with source locations, exposes valued expressions when `expr_externally_used` requires it, and defers wire reads to the external wiring machinery when applicable.

**`visit_block`**: Manages conditional and cycled blocks by maintaining a condition stack for proper predicate generation; when a conditional contains logging or other side effects it exposes the condition to the outside world to keep the testbench accurate.

**`expose`**: Registers expressions that need to be exposed as module outputs, handling different types (expr, array, fifo, trigger). The collected metadata ultimately drives both module-level port declarations and the top-level harness wiring.

**`get_pred`**: Generates the current execution predicate by combining all conditions in the condition stack

**`get_external_port_name`**: Creates mangled port names for external values to avoid naming conflicts

**Project-specific Knowledge Required**:
- Understanding of [visitor pattern](/python/assassyn/ir/visitor.md)
- Knowledge of [credit-based pipeline architecture](/docs/design/arch/arch.md)
- Understanding of [module generation](/python/assassyn/codegen/verilog/module.md)
- Reference to [array management](/python/assassyn/codegen/verilog/cleanup.md)
- Knowledge of [external module integration](/python/assassyn/ir/module/external.md)

#### Internal Helpers

**`_walk_expressions`**: Recursively traverses blocks to find all expressions for analysis

**`_generate_external_module_wrapper`**: Creates PyCDE wrapper classes for external SystemVerilog modules. If the external metadata defines explicit wires (and their direction), the wrapper mirrors them; otherwise it falls back to treating the declared ports as inputs for backwards compatibility. Clock/reset ports are emitted when requested by the metadata.

**`_connect_array`**: Handles multi-port array connections between modules by wiring each module’s per-port write enable/data/index signals into the shared array writer instance the dumper previously generated.

**`_is_external_module`**: Determines if a module represents an external implementation

The CIRCTDumper class integrates with multiple other modules:
- [Expression generation](/python/assassyn/codegen/verilog/_expr/__init__.md) for handling different expression types
- [Cleanup processing](/python/assassyn/codegen/verilog/cleanup.md) for post-generation signal routing
- [Module port generation](/python/assassyn/codegen/verilog/module.md) for interface creation
- [System generation](/python/assassyn/codegen/verilog/system.md) for top-level integration
- [Right-hand value generation](/python/assassyn/codegen/verilog/rval.md) for signal references

**Project-specific Knowledge Required**:
- Understanding of [CIRCT framework](/docs/design/internal/pipeline.md)
- Knowledge of [PyCDE integration](/docs/design/internal/pipeline.md)
- Understanding of [multi-port array architecture](/docs/design/arch/arch.md)
- Reference to [external module system](/python/assassyn/ir/module/external.md)
