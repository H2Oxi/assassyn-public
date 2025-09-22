'''External module implementation for integrating SystemVerilog modules.'''

# pylint: disable=duplicate-code

import os

from ...builder import Singleton
from ..expr import wire_assign, wire_read
from .module import Module, Wire, Port


class DirectionalWires:
    """Adapter exposing directional wire access consistent with the simplified API."""

    def __init__(self, ext_module, direction):
        self._module = ext_module
        self._direction = direction

    def _get_wire(self, key):
        wire = self._module._wires.get(key)
        if wire is None:
            raise KeyError(f"Wire '{key}' not found")
        if wire.direction != self._direction:
            raise ValueError(f"Wire '{key}' is not an {self._direction} wire")
        return wire

    def __contains__(self, key):
        wire = self._module._wires.get(key)
        return wire is not None and wire.direction == self._direction

    def __getitem__(self, key):
        wire = self._get_wire(key)
        if self._direction == 'output':
            return wire_read(wire)
        return wire.value

    def __setitem__(self, key, value):
        if self._direction != 'input':
            raise ValueError(f"Cannot assign to '{key}' on output wires")
        wire = self._get_wire(key)
        wire_assign(wire, value)
        wire.assign(value)

    def keys(self):
        return [name for name, wire in self._module._wires.items()
                if wire.direction == self._direction]

class ExternalSV(Module):
    '''An external module implemented in SystemVerilog.'''

    # pylint: disable=too-many-arguments,too-many-locals,too-many-branches,too-many-statements
    def __init__(
        self,
        file_path,
        in_wires=None,
        out_wires=None,
        module_name=None,
        no_arbiter=False,
        has_clock=False,
        has_reset=False,
        **wire_connections,
    ):
        '''Construct an external module.

        Args:
            file_path (str): Path to the SystemVerilog file containing the module.
            in_wires (dict, optional): Named input wire definitions `{name: dtype}`.
            out_wires (dict, optional): Named output wire definitions `{name: dtype}`.
            module_name (str, optional): Name of the module in the SystemVerilog file.
                                        Defaults to the class name.
            no_arbiter (bool): Whether to disable arbiter rewriting.
            **wire_connections: Optional initial assignments for declared input wires.
        '''
        # Store external file information
        # Normalize the file path to handle both absolute and relative paths
        if os.path.isabs(file_path):
            self.file_path = file_path
        else:
            # If it's a relative path, store it as is and resolve it during elaboration
            self.file_path = file_path

        self.external_module_name = module_name or type(self).__name__
        self.has_clock = has_clock
        self.has_reset = has_reset

        self._wires = {}

        def _register_wire(name, dtype, direction, port_map):
            wire = Wire(dtype, direction, self)
            wire.name = name
            port_map[name] = Port(dtype)
            self._wires[name] = wire

        port_defs = {}
        if in_wires:
            for wire_name, dtype in in_wires.items():
                _register_wire(wire_name, dtype, 'input', port_defs)
        if out_wires:
            for wire_name, dtype in out_wires.items():
                _register_wire(wire_name, dtype, 'output', port_defs)

        # Initialize as regular module
        super().__init__(port_defs, no_arbiter)

        # Provide directional accessors (always available for convenience)
        self.in_wires = DirectionalWires(self, 'input')
        self.out_wires = DirectionalWires(self, 'output')

        # Add attribute to mark as external
        self._attrs[Module.ATTR_EXTERNAL] = True

        # Handle wire connections passed as keyword arguments (only for declared inputs)
        for wire_name, value in wire_connections.items():
            wire_obj = self._wires.get(wire_name)
            if wire_obj is None:
                raise KeyError(f"Cannot assign to undefined wire '{wire_name}'")
            if wire_obj.direction != 'input':
                raise ValueError(
                    "Cannot assign to output wire "
                    f"'{wire_name}' during initialization"
                )
            wire_assign(wire_obj, value)
            wire_obj.assign(value)

    def __setitem__(self, key, value):
        '''Allow assignment to wires using bracket notation.'''
        if key in self.in_wires:
            self.in_wires[key] = value
            return
        raise KeyError(f"Wire '{key}' not found")

    def __getitem__(self, key):
        '''Allow access to wires using bracket notation.'''
        if key in self.out_wires:
            return self.out_wires[key]
        if key in self.in_wires:
            return self.in_wires[key]
        raise KeyError(f"Wire '{key}' not found")

    def in_assign(self, **kwargs):
        '''Assign values to input wires using keyword arguments.

        Args:
            **kwargs: Wire name to value mappings (e.g., a=value, b=value)
        '''
        for wire_name, value in kwargs.items():
            self.in_wires[wire_name] = value

    def __repr__(self):
        '''String representation of the external module.'''
        ports = '\n    '.join(repr(v) for v in self.ports)
        if ports:
            ports = f'{{\n    {ports}\n  }} '
        attrs = ', '.join(f'{Module.MODULE_ATTR_STR[i]}: {j}' for i, j in self._attrs.items())
        attrs = f'#[{attrs}] ' if attrs else ''
        var_id = self.as_operand()

        # Add external file information to representation
        ext_info = f'  // External file: {self.file_path}\n'
        ext_info += f'  // External module name: {self.external_module_name}\n'

        Singleton.repr_ident = 2
        body = self.body.__repr__() if self.body else ''
        ext = self._dump_externals()
        return f'''{ext}{ext_info}  {attrs}
  {var_id} = external_module {self.name} {ports}{{
{body}
  }}'''
