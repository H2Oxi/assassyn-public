'''The module for intrinsic expressions'''
#pylint: disable=cyclic-import

from ...builder import ir_builder
from .expr import Expr

INTRIN_INFO = {
    # Intrinsic operations opcode: (mnemonic, num of args, valued, side effect)
    900: ('wait_until', 1, False, True),
    901: ('finish', 0, False, True),
    902: ('assert', 1, False, True),
    903: ('barrier', 1, False, True),
    904: ('has_mem_resp', 1, False, True),
    905: ('mem_write', 3, False, True),
    906: ('send_read_request', 2, False, True),
    907: ('mem_resp', 1, True, False),
    908: ('send_write_request', 3, False, True),
    909: ('use_dram', 1, False, True),
    910: ('read_request_succ', 1, False, True),
    911: ('write_request_succ', 1, False, True),
    912: ('get_mem_resp', 1, True, False),
}


class Intrinsic(Expr):
    '''The class for intrinsic operations'''

    WAIT_UNTIL = 900
    FINISH = 901
    ASSERT = 902
    BARRIER = 903
    HAS_MEM_RESP = 904
    MEM_WRITE = 905
    SEND_READ_REQUEST = 906
    MEM_RESP = 907
    SEND_WRITE_REQUEST = 908
    USE_DRAM = 909
    READ_REQUEST_SUCC = 910
    WRITE_REQUEST_SUCC = 911
    GET_MEM_RESP = 912

    opcode: int  # Operation code for this intrinsic

    def __init__(self, opcode, *args):
        super().__init__(opcode, args)
        _, num_args, _, _ = INTRIN_INFO[opcode]
        if num_args is not None:
            assert len(args) == num_args

    @property
    def args(self):
        '''Get the arguments of this intrinsic.'''
        return self._operands

    @property
    def dtype(self):
        '''Get the data type of this intrinsic.'''
        # pylint: disable=import-outside-toplevel
        from ..dtype import Bits

        if self.opcode in [
            Intrinsic.HAS_MEM_RESP,
            Intrinsic.SEND_READ_REQUEST,
            Intrinsic.SEND_WRITE_REQUEST,
            Intrinsic.READ_REQUEST_SUCC,
            Intrinsic.WRITE_REQUEST_SUCC,
        ]:
            return Bits(1)
        if self.opcode in [Intrinsic.MEM_RESP, Intrinsic.GET_MEM_RESP]:
            return Bits(self.args[0].width)
        return Bits(1)

    def __repr__(self):
        args = {", ".join(i.as_operand() for i in self.args[0:])}
        mn, _, valued, side_effect = INTRIN_INFO[self.opcode]
        side_effect = ['', 'side effect '][side_effect]
        rhs = f'{side_effect}intrinsic.{mn}({args})'
        if valued:
            return f'{self.as_operand()} = {rhs}'
        return rhs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):  # pylint: disable=unused-argument
        return False


@ir_builder
def wait_until(cond):
    '''Frontend API for creating a wait-until block.'''
    # pylint: disable=import-outside-toplevel
    from ..value import Value
    assert isinstance(cond, Value)
    return Intrinsic(Intrinsic.WAIT_UNTIL, cond)


@ir_builder
def assume(cond):
    '''Frontend API for creating an assertion.
    This name is to avoid conflict with the Python keyword.'''
    # pylint: disable=import-outside-toplevel
    from ..value import Value
    assert isinstance(cond, Value)
    return Intrinsic(Intrinsic.ASSERT, cond)


def is_wait_until(expr):
    '''Check if the expression is a wait-until intrinsic.'''
    return isinstance(expr, Intrinsic) and expr.opcode == Intrinsic.WAIT_UNTIL


@ir_builder
def finish():
    '''Finish the simulation.'''
    return Intrinsic(Intrinsic.FINISH)


@ir_builder
def barrier(node):
    '''Barrier the current simulation state.'''
    return Intrinsic(Intrinsic.BARRIER, node)


@ir_builder
def has_mem_resp(memory):
    '''Check if there is a memory response.'''
    return Intrinsic(Intrinsic.HAS_MEM_RESP, memory)


@ir_builder
def mem_write(payload, addr, wdata):
    '''Memory write operation.'''
    return Intrinsic(Intrinsic.MEM_WRITE, payload, addr, wdata)


@ir_builder
def send_read_request(mem, addr):
    '''Send a read request with address to the given memory system.'''
    return Intrinsic(Intrinsic.SEND_READ_REQUEST, mem, addr)


@ir_builder
def send_write_request(mem, addr, data):
    '''Send a write request with address and data to the given memory system.'''
    return Intrinsic(Intrinsic.SEND_WRITE_REQUEST, mem, addr, data)


@ir_builder
def mem_resp(memory):
    '''Get the memory response.'''
    return Intrinsic(Intrinsic.MEM_RESP, memory)


@ir_builder
def use_dram(dram):
    '''Use a DRAM module.'''
    return Intrinsic(Intrinsic.USE_DRAM, dram)


@ir_builder
def read_request_succ(mem):
    '''Check if the read request sent in this cycle succeeds.'''
    return Intrinsic(Intrinsic.READ_REQUEST_SUCC, mem)


@ir_builder
def write_request_succ(mem):
    '''Check if the write request sent in this cycle succeeds.'''
    return Intrinsic(Intrinsic.WRITE_REQUEST_SUCC, mem)


@ir_builder
def get_mem_resp(mem):
    '''Get the memory response data. The LSB are the data payload, the MSB the request address.'''
    return Intrinsic(Intrinsic.GET_MEM_RESP, mem)


class PureIntrinsic(Expr):
    '''The class for accessing FIFO fields, valid, and peek'''

    # FIFO operations
    FIFO_VALID = 300
    FIFO_PEEK = 303
    MODULE_TRIGGERED = 304
    VALUE_VALID = 305

    OPERATORS = {
        FIFO_VALID: 'valid',
        FIFO_PEEK: 'peek',
        MODULE_TRIGGERED: 'triggered',
        VALUE_VALID: 'valid',
    }

    def __init__(self, opcode, *args):
        operands = list(args)
        super().__init__(opcode, operands)

    @property
    def args(self):
        '''Get the arguments of this intrinsic'''
        return self._operands

    @property
    def dtype(self):
        '''Get the data type of this intrinsic'''
        # pylint: disable=import-outside-toplevel
        from ..dtype import Bits

        if self.opcode == PureIntrinsic.FIFO_PEEK:
            # pylint: disable=import-outside-toplevel
            from ..module import Port
            fifo = self.args[0]
            assert isinstance(fifo, Port)
            return fifo.dtype

        if self.opcode in [
            PureIntrinsic.FIFO_VALID,
            PureIntrinsic.MODULE_TRIGGERED,
            PureIntrinsic.VALUE_VALID,
        ]:
            return Bits(1)

        raise ValueError(f"Unknown intrinsic opcode {self.opcode}")
