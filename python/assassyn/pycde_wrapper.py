"""Runtime PyCDE helpers shared between generated and hand-authored designs."""

from __future__ import annotations
# pylint: disable=invalid-name,unused-argument,import-error,too-few-public-methods

from pycde import Clock, Input, Module, Output, Reset
from pycde import modparams
from pycde.types import Bits

__all__ = ("FIFO", "TriggerCounter", "build_register_file")


@modparams
def FIFO(WIDTH: int, DEPTH_LOG2: int):
    """Depth-parameterized FIFO matching the backend's SystemVerilog resource."""

    class FIFOImpl(Module):
        """PyCDE module for the backend FIFO primitive."""
        module_name = "fifo"
        # Define inputs
        clk = Clock()
        rst_n = Input(Bits(1))
        push_valid = Input(Bits(1))
        push_data = Input(Bits(WIDTH))
        pop_ready = Input(Bits(1))
        # Define outputs
        push_ready = Output(Bits(1))
        pop_valid = Output(Bits(1))
        pop_data = Output(Bits(WIDTH))

    return FIFOImpl


@modparams
def TriggerCounter(WIDTH: int):
    """Credit counter primitive used to gate driver execution."""

    class TriggerCounterImpl(Module):
        """PyCDE module mirroring the trigger_counter primitive."""
        module_name = "trigger_counter"
        clk = Clock()
        rst_n = Input(Bits(1))
        delta = Input(Bits(WIDTH))
        delta_ready = Output(Bits(1))
        pop_ready = Input(Bits(1))
        pop_valid = Output(Bits(1))

    return TriggerCounterImpl


def build_register_file(  # pylint: disable=too-many-arguments
    module_name,
    data_type,
    depth,
    num_write_ports,
    num_read_ports,
    *,
    addr_width=None,
    include_read_index=True,
    initializer=None,
):
    """Create an external multi-port register file module wrapper.

    The Verilog backend emits a synthesizable SystemVerilog implementation for each
    register file (one per array) alongside the compiled CIRCT output. This helper
    only declares the ports so CIRCT can instantiate the module by name.
    """
    computed_addr_width = max(1, (depth - 1).bit_length()) if depth > 0 else 1
    if addr_width is None:
        addr_width = computed_addr_width
    addr_width = max(1, addr_width)

    if initializer is not None:
        if len(initializer) != depth:
            raise ValueError(
                f"Initializer length {len(initializer)} does not match depth {depth}"
            )

    attrs = {
        "module_name": module_name,
        "clk": Clock(),
        "rst": Reset(),
        "ADDR_WIDTH": addr_width,
        "DEPTH": depth,
        "NUM_WRITE_PORTS": num_write_ports,
        "NUM_READ_PORTS": num_read_ports,
    }

    for w_idx in range(num_write_ports):
        attrs[f"w_port{w_idx}"] = Input(Bits(1))
        attrs[f"widx_port{w_idx}"] = Input(Bits(addr_width))
        attrs[f"wdata_port{w_idx}"] = Input(data_type)

    if include_read_index:
        for r_idx in range(num_read_ports):
            attrs[f"ridx_port{r_idx}"] = Input(Bits(addr_width))

    for r_idx in range(num_read_ports):
        attrs[f"rdata_port{r_idx}"] = Output(data_type)

    return type(module_name, (Module,), attrs)
