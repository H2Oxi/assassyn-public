"""Call and wire operations code generation for Verilog.

This module contains functions to generate Verilog code for call operations,
including AsyncCall, Bind, WireAssign, and WireRead.
"""

from typing import Optional

from ....ir.expr import AsyncCall, WireAssign, WireRead
from ....ir.expr.call import Bind


def codegen_async_call(dumper, expr: AsyncCall) -> Optional[str]:
    """Generate code for async call operations."""
    dumper.expose('trigger', expr)


def codegen_bind(_dumper, _expr: Bind) -> Optional[str]:
    """Generate code for bind operations.

    Bind operations don't generate any code, they just represent bindings.
    """


def codegen_wire_assign(dumper, expr: WireAssign) -> Optional[str]:
    """Generate code for wire assign operations."""
    return f"# Wire assign: {expr}"


def codegen_wire_read(dumper, expr: WireRead) -> Optional[str]:
    """Generate code for wire read operations."""
    rval = dumper.dump_rval(expr, False)
    wire = expr.wire
    wire_name = getattr(wire, 'name', None)
    owner = getattr(wire, 'parent', None) or getattr(wire, 'module', None)

    if owner is dumper.current_module and wire_name:
        return f"{rval} = self.{wire_name}"

    return f"# Wire read: {expr}"
