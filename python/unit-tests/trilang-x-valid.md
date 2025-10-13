# Triple Language Ramulator2 Cross Validation

The `compare_ramulator2_outputs.py` script runs three different Ramulator2 wrapper implementations and compares their outputs to ensure behavioral consistency.

## Core Functionality

The script runs these three implementations with the same configuration and request sequence:

- **C++ Implementation**: `tools/c-ramulator2-wrapper/test.cpp` (executable: `build/bin/test`)
- **Rust Implementation**: `tools/rust-sim-runtime/tests/test_ramulator2.rs` (via `cargo test`)
- **Python Implementation**: `python/unit-tests/test_ramulator2.py`

## Usage

### Basic Usage
```bash
python python/unit-tests/compare_ramulator2_outputs.py
```

### Command Line Options

- `--skip <language>`: Skip running a specific language implementation
- `--debug`: Enable verbose debugging output
- `--show-outputs`: Display raw outputs before comparison

## Output Processing

The script normalizes outputs for fair comparison:
1. Removes Rust test harness noise (`running 1 test`, `test result: ok...`)
2. Strips blank lines and trailing whitespace
3. Compares normalized outputs

## Return Codes

- **0**: All outputs are identical
- **1**: Outputs differ (shows unified diff)
- **2**: Command execution failed

## Example Output

### Success
```bash
$ python python/unit-tests/compare_ramulator2_outputs.py
All outputs are identical across implementations.
```

### Failure with Differences
```bash
$ python python/unit-tests/compare_ramulator2_outputs.py
[DIFF] cpp vs rust:
--- cpp
+++ rust
@@ -1,3 +1,2 @@
 Cycle 3: Write request sent for address 2, success or not (true or false)true
 Cycle 9: Request completed: 2 the data is: 1
-Cycle 5: Write request sent for address 4, success or not (true or false)true
+Cycle 5: Write request sent for address 4, success or not (true or false)false
```

## Related Files

- `python/unit-tests/test_ramulator2.py`: Python implementation
- `tools/c-ramulator2-wrapper/test.cpp`: C++ implementation  
- `tools/rust-sim-runtime/tests/test_ramulator2.rs`: Rust implementation
