'''Programming interfaces exposes as the frontend of assassyn'''

#pylint: disable=unused-import
from .ir.array import RegArray, Array
from .ir.dtype import DType, Int, UInt, Float, Bits, Record
from .builder import SysBuilder, ir_builder, Singleton
from .ir.expr import Expr, log, concat, finish, wait_until, assume, barrier, mem_read, mem_write
from .ir.module import Module, Port, Downstream, fsm, downstream_combinational
from .ir.module.external import ExternalModule
from .ir.module.sram import SRAM
from .ir.block import Condition, Cycle
from .ir import module
from .ir.value import Value

# Create a downstream module object with combinational attribute
class downstream:  # pylint: disable=too-few-public-methods, invalid-name
    """Frontend helper exposing the downstream combinational attribute."""

    combinational = downstream_combinational
