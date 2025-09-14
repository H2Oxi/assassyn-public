'''The module AST implementation.'''

from __future__ import annotations

import typing
from decorator import decorator

from ...builder import Singleton, ir_builder
from ..block import Block
from ..expr import Bind, FIFOPop, PureIntrinsic, FIFOPush, AsyncCall, Expr
from ..expr.intrinsic import wait_until
from .base import ModuleBase

if typing.TYPE_CHECKING:
    from ..dtype import DType
    from ..value import Value

def _reserved_module_name(name):
    return name in ['Driver', 'Testbench']

#pylint: disable=too-few-public-methods
class Timing:
    '''The enum class for the timing policy of a module.'''
    SYSTOLIC = 1  # Systolic timing policy
    BACKPRESSURE = 2  # Backpressure timing policy

    @staticmethod
    def to_string(value):
        '''The helper function to convert the timing policy to string.'''
        return [None, 'systolic', 'backpressure'][value]

class Module(ModuleBase):
    '''The AST node for defining a module.'''

    body: Block  # Body of the module
    name: str  # Name of the module
    _attrs: dict  # Dictionary of module attributes
    _ports: list  # List of ports
    _users: typing.List[Expr]  # Callers of this module

    ATTR_DISABLE_ARBITER = 1
    ATTR_TIMING = 2
    ATTR_MEMORY = 3
    ATTR_EXTERNAL = 4

    MODULE_ATTR_STR = {
      ATTR_DISABLE_ARBITER: 'no_arbiter',
      ATTR_MEMORY: 'memory',
      ATTR_TIMING: 'timing',
      ATTR_EXTERNAL: 'external',
    }

    def __init__(self, ports, no_arbiter=False):
        '''Construct the module with the given attributes.

        Args:
          - explicit_fifo(bool): If this module explicitly pops FIFO values.
          - timing(Timing): The timing policy of this module.
          - disable_arbiter_rewrite(bool): When there are multiple callers, if this module
          should be rewritten by the compiler.
          - ports: The ports of this module.
        '''
        super().__init__()
        self.body = None
        self.name = type(self).__name__
        if not _reserved_module_name(self.name):
            self.name = self.name + self.as_operand()

        self._attrs = {}
        if no_arbiter:
            self._attrs[Module.ATTR_DISABLE_ARBITER] = True

        self._ports = []
        for name, port in ports.items():
            assert isinstance(port, Port)
            setattr(self, name, port)
            port.name = name
            port.module = self
            self._ports.append(getattr(self, name))
        self._users = []

        assert Singleton.builder is not None, 'Cannot instantitate a module outside of a system!'
        Singleton.builder.modules.append(self)

    @property
    def users(self):
        '''The helper function to get all the users of this module.'''
        return self._users

    @property
    def ports(self):
        '''The helper function to get all the ports in the module.'''
        return self._ports

    def validate_all_ports(self):
        '''A syntactic sugar for checking if all the port FIFOs have value inside.'''
        valid = None
        for port in self.ports:
            valid = port.valid() if valid is None else valid & port.valid()

        wait_until(valid)

    def pop_all_ports(self, validate):
        '''A syntactic sugar for popping all the ports in this module.'''
        assert self.timing is None, 'Cannot pop ports in a systolic module!'
        if validate:
            self.validate_all_ports()
            self.timing = Timing.BACKPRESSURE
        else:
            self.timing = Timing.SYSTOLIC

        res = [port.pop() for port in self.ports]

        return res if len(res) > 1 else res[0]

    @ir_builder
    def async_called(self, **kwargs):
        '''The frontend API for creating an async call operation to this `self` module.'''
        bind = self.bind(**kwargs)
        return AsyncCall(bind)

    @ir_builder
    def bind(self, **kwargs):
        '''The frontend API for creating a bind operation to this `self` module.'''
        bound = Bind(self, **kwargs)
        return bound

    def __repr__(self):
        ports = '\n    '.join(repr(v) for v in self.ports)
        if ports:
            ports = f'{{\n    {ports}\n  }} '
        attrs = ', '.join(f'{Module.MODULE_ATTR_STR[i]}: {j}' for i, j in self._attrs.items())
        attrs = f'#[{attrs}] ' if attrs else ''
        var_id = self.as_operand()

        Singleton.repr_ident = 2
        body = self.body.__repr__()
        ext = self._dump_externals()
        return f'''{ext}  {attrs}
  {var_id} = module {self.name} {ports}{{
{body}
  }}'''

    @property
    def is_systolic(self):
        '''The helper function to get if this module is systolic.'''
        return self.timing == Timing.SYSTOLIC

    @property
    def timing(self):
        '''The helper function to get the timing policy of this module.'''
        return self._attrs.get(Module.ATTR_TIMING, None)

    @timing.setter
    def timing(self, value):
        '''The helper function to set the timing policy of this module.'''
        assert Module.ATTR_TIMING not in self._attrs, 'Cannot set timing twice!'
        if isinstance(value, str):
            value = {'systolic': Timing.SYSTOLIC, 'backpressure': Timing.BACKPRESSURE}[value]
        self._attrs[Module.ATTR_TIMING] = value

    @property
    def no_arbiter(self):
        '''The helper function to get the no-arbiter setting.'''
        return self._attrs.get(Module.ATTR_DISABLE_ARBITER, False)

class Port:
    '''The AST node for defining a port in modules.'''

    dtype: DType  # Data type of the port
    name: str  # Name of the port
    module: Module  # Module this port belongs to
    _users: typing.List[Expr]  # Users of the port

    def __init__(self, dtype: DType):
        #pylint: disable=import-outside-toplevel
        from ..dtype import DType
        assert isinstance(dtype, DType)
        self.dtype = dtype
        self.name = self.module = None
        self._users = []

    @property
    def users(self):
        '''Get the users of the port.'''
        return self._users

    @ir_builder
    def valid(self):
        '''The frontend API for creating a FIFO.valid operation.'''
        return PureIntrinsic(PureIntrinsic.FIFO_VALID, self)

    @ir_builder
    def peek(self):
        '''The frontend API for creating a FIFO.peek operation.'''
        return PureIntrinsic(PureIntrinsic.FIFO_PEEK, self)

    @ir_builder
    def pop(self):
        '''The frontend API for creating a pop operation.'''
        return FIFOPop(self)

    @ir_builder
    def push(self, v):
        '''The frontend API for creating a push operation.'''
        return FIFOPush(self, v)

    def __repr__(self):
        return f'{self.name}: Port<{self.dtype}>'

    def as_operand(self):
        '''Dump the port as a right-hand side reference.'''
        return f'{self.module.as_operand()}.{self.name}'

@decorator
#pylint: disable=keyword-arg-before-vararg
def combinational(
        func,
        *args,
        **kwargs):
    '''A decorator for marking a function as combinational logic description.'''
    module_self = args[0]
    assert isinstance(module_self, Module)
    module_self.body = Block(Block.MODULE_ROOT)
    Singleton.builder.enter_context_of('module', module_self)
    # TODO(@were): Make implicit pop more robust.
    with module_self.body:
        res = func(*args, **kwargs)
    Singleton.builder.exit_context_of('module')
    return res


class Wire:
    '''A wire for connecting to external modules.'''
    
    def __init__(self, dtype, direction=None):
        #pylint: disable=import-outside-toplevel
        from ..dtype import DType
        if dtype is not None:
            assert isinstance(dtype, DType)
        self.dtype = dtype
        self.direction = direction  # 'input', 'output', or None (undirected)
        self.value = None  # Assigned value for input wires
        
    def __repr__(self):
        dir_str = f", {self.direction}" if self.direction else ""
        return f'Wire<{self.dtype}{dir_str}>'
        
    def assign(self, value):
        '''Assign a value to this wire (for input wires).'''
        if self.direction == 'output':
            raise ValueError("Cannot assign to output wire")
        self.value = value
        
    def get_value(self):
        '''Get the assigned value (for input wires) or the wire itself (for output wires).'''
        if self.direction == 'input':
            return self.value
        return self  # For output wires, return the wire itself


class WireDict:
    '''A dictionary-like class for managing wires with assignment and access support.'''
    
    def __init__(self):
        self._wires = {}
        
    def __setitem__(self, key, value):
        '''Assign a value to a wire.'''
        if key in self._wires:
            # If wire already has a direction and it's output, this is an error
            if self._wires[key].direction == 'output':
                raise ValueError(f"Cannot assign to output wire '{key}'")
            # If wire is undirected, set its direction to input
            if self._wires[key].direction is None:
                self._wires[key].direction = 'input'
            self._wires[key].assign(value)
        else:
            # Create a new wire with input direction
            from ..dtype import DType
            if hasattr(value, 'dtype') and isinstance(value.dtype, DType):
                self._wires[key] = Wire(value.dtype, 'input')
                self._wires[key].assign(value)
            else:
                raise KeyError(f"Cannot create wire '{key}' - cannot infer type from value")
            
    def __getitem__(self, key):
        '''Get a wire's value or the wire itself.'''
        if key in self._wires:
            # If this is the first time accessing this wire, set its direction to output
            if self._wires[key].direction is None:
                self._wires[key].direction = 'output'
            return self._wires[key].get_value()
        else:
            # Create a new wire with output direction
            self._wires[key] = Wire(None, 'output')  # Type will be determined later
            return self._wires[key].get_value()
            
    def __contains__(self, key):
        '''Check if a wire exists.'''
        return key in self._wires
        
    def __repr__(self):
        return f'WireDict({self._wires})'
