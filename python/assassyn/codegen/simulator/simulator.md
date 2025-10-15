# Simulator Generation

This module generates Rust-based simulators from Assassyn systems. It implements the credit-based pipeline architecture described in the [simulator design document](../../../docs/design/internal/simulator.md) by translating Assassyn operations into corresponding Rust operations with proper handling of register writes, stage registers, and asynchronous calls.

## Section 0. Summary

The simulator generation process creates a complete Rust project that faithfully executes the high-level execution model of Assassyn hardware designs. The generated simulator implements the credit-based pipeline architecture where pipeline stages communicate through event queues and FIFOs, while downstream modules execute as pure combinational logic driven by upstream stage triggers. Beyond array port arbitration and DRAM simulation, the generator now wires in external SystemVerilog FFIs: it tracks value exposures needed by external modules, carries per-module handle structs inside the simulator state, and knows how to tick externally clocked peripherals alongside the internal register model.

## Section 1. Exposed Interfaces

### analyze_and_register_ports

```python
def analyze_and_register_ports(sys):
    """Analyze system and register all array write ports and DRAM modules.

    This function scans the entire system to find all array writes and DRAM modules,
    registers them with the port manager, ensuring each writer gets a unique
    port index for compile-time port allocation.

    Args:
        sys: The Assassyn system builder

    Returns:
        Tuple of (port_manager, dram_modules) where dram_modules is a list of DRAM instances
    """
```

**Explanation:**

This function performs a comprehensive analysis of the Assassyn system to prepare for simulator generation. It uses a dedicated visitor to traverse all expressions and modules, identifying two key components:

1. **Array Write Port Registration**: For each `ArrayWrite` expression, it registers the array/module combination with the global port manager. Each writer is assigned a stable port index so the generated simulator can allocate fixed write ports up front.

2. **DRAM Module Collection**: It collects every `DRAM` instance so the generator can allocate per-DRAM `MemoryInterface`s and response buffers. The legacy `MEM_WRITE` intrinsic has been removed, so array writes are the only source of port registrations.

The function returns both the port manager (with its assigned port counts) and the list of DRAM modules. `dump_simulator` consumes these results to shape the simulator struct and to initialise request/response bookkeeping for every memory device.

### dump_simulator

```python
def dump_simulator(sys: SysBuilder, config, fd):
    """Generate the simulator module.

    This matches the Rust function in src/backend/simulator/elaborate.rs

    Args:
        sys: The Assassyn system builder
        config: Configuration dictionary with the following keys:
            - idle_threshold: Idle threshold for the simulator
            - sim_threshold: Maximum number of simulation cycles
            - random: Whether to randomize module execution order
            - resource_base: Path to resource files
            - fifo_depth: Default FIFO depth
        fd: File descriptor to write to
    """
```

**Explanation:**

This function generates the complete Rust simulator implementation by writing to the provided file descriptor. The generation process follows these steps:

1. **System Analysis**: Calls `analyze_and_register_ports` to determine array-port requirements and collect DRAM modules. It also builds a lookup table of external FFI specs from `config.get("external_ffis", [])`.

2. **Import Generation**: Writes the Rust `use` statements required by the generated code (`sim_runtime`, `VecDeque`, `HashMap`, `SliceRandom`, dynamic library helpers, etc.).

3. **Simulator Struct Generation**: Creates the main `Simulator` struct with fields for:
   - Global timestamp and `request_stamp_map_table` (used to pair DRAM responses with the issue stamp)
   - Per-DRAM `MemoryInterface` instances and `Response` buffers
   - Register arrays with ports sized according to the port manager
   - Module trigger flags, event queues, and FIFO buffers
   - External FFI handles for every `ExternalSV` module that participates in co-simulation (recording which handles require clock ticks)
   - Optional `<expr>_value` slots for every IR value that must be visible outside its defining module (computed via `gather_expr_validities`)

4. **Implementation Generation**: Generates the `impl Simulator` block with methods for:
   - Constructor (`new`) that initialises DRAM interfaces, arrays, FIFOs, FFI handles, and expression caches
   - `event_valid`, `reset_downstream`, `tick_registers`, and `reset_dram` helpers. `tick_registers` now also pulses any external handles that expose a clock tick API.

5. **Module Simulation Functions**: Emits `simulate_<module_name>` methods that:
   - Guard execution based on event queues or upstream triggers
   - Call into `modules::<module_name>` and interpret the boolean return (popping events on success, clearing exposed values on failure)
   - Track `triggered` flags so the top-level loop can detect activity

6. **Main Simulation Loop**: Generates the `simulate()` function which:
   - Instantiates `Simulator::new()` and initialises each DRAM interface with a configuration file
   - Builds vectors of stage and downstream simulation functions, optionally shuffling stage order when `config["random"]` is truthy
   - Seeds Driver/Testbench event queues, loads SRAM payloads from resource files, and honours `idle_threshold` when the design goes quiescent
   - Ticks registers, clocks external handles, and advances DRAM interfaces every iteration

Configuration parameters such as `sim_threshold`, `idle_threshold`, `random`, `resource_base`, `fifo_depth`, and `external_ffis` flow from the `config` dictionary. The generated simulator continues to implement the credit-based pipeline architecture documented in [simulator.md](../../../docs/design/internal/simulator.md) while adding first-class support for co-simulated external modules.

## Section 2. Internal Helpers

### PortRegistrationVisitor

```python
class PortRegistrationVisitor(Visitor):
    """Visitor that registers array write ports and collects DRAM modules."""
```

**Explanation:**

This internal visitor class is used by `analyze_and_register_ports` to traverse the Assassyn system and perform the necessary analysis. It implements the visitor pattern to systematically examine all expressions and modules:

- **Expression Visiting**: When visiting expressions, it specifically looks for `ArrayWrite` instances and registers them with the port manager using the array name and module name as keys.

- **Module Visiting**: When visiting modules, it identifies `DRAM` instances and adds them to the collection for later use in memory interface generation.

The visitor pattern ensures that all array writes and DRAM modules are discovered regardless of their location in the system hierarchy, providing comprehensive coverage for port allocation and memory interface setup.

### Configuration Parameters

The `dump_simulator` function accepts several configuration parameters that control the generated simulator behavior:

- **idle_threshold**: Controls when the simulation stops due to inactivity (default: 5)
- **sim_threshold**: Maximum number of simulation cycles (default: 100)  
- **random**: Whether to randomize module execution order for testing
- **resource_base**: Base path for resource files (SRAM initialization)
- **fifo_depth**: Default depth for FIFO implementations

These parameters allow fine-tuning of the simulator behavior for different testing scenarios and performance requirements.

### Memory Interface Management

The simulator generation creates per-DRAM memory interfaces rather than a single global interface. This approach provides better isolation and callback management for systems with multiple DRAM modules. Each DRAM module gets:

- A dedicated `MemoryInterface` instance (`mi_<dram_name>`)
- A response buffer (`<dram_name>_response`) for handling memory responses
- Proper initialization with configuration files
- Individual ticking in the simulation loop

This design matches the requirements described in the [simulator design document](../../../docs/design/internal/simulator.md) for handling multiple memory interfaces in complex systems.
