'''External module implementation for integrating SystemVerilog modules.'''

import os
from .module import Module, WireDict
from ...utils import namify
from ..expr import wire_assign

class ExternalModule(Module):
    '''An external module implemented in SystemVerilog.'''
    
    def __init__(self, file_path, ports=None, wires=None, in_wires=None, out_wires=None, module_name=None, no_arbiter=False, **wire_connections):
        '''Construct an external module.
        
        Args:
            file_path (str): Path to the SystemVerilog file containing the module
            ports (dict, optional): Port definitions matching the external module's interface
            wires (dict, optional): Wire definitions as an alternative to ports
            in_wires (dict, optional): Input wire definitions
            out_wires (dict, optional): Output wire definitions
            module_name (str, optional): Name of the module in the SystemVerilog file.
                                       Defaults to the class name.
            no_arbiter (bool): Whether to disable arbiter rewriting
            **wire_connections: Wire connections as keyword arguments (e.g., a=value, b=value)
        '''
        # Store external file information
        # Normalize the file path to handle both absolute and relative paths
        if os.path.isabs(file_path):
            self.file_path = file_path
        else:
            # If it's a relative path, store it as is and resolve it during elaboration
            self.file_path = file_path
            
        self.external_module_name = module_name or type(self).__name__
        
        # Handle wires parameter as alternative to ports
        if wires is not None:
            # Create ports from wires for backward compatibility
            if ports is None:
                ports = {}
            # Create wire dictionary for direct access
            self.wires = WireDict()
            from .module import Wire
            for name, dtype in wires.items():
                # Create undirected wires initially, directions will be inferred from usage
                self.wires._wires[name] = Wire(dtype)
                # For backward compatibility, we still create ports
                from .module import Port
                ports[name] = Port(dtype)
        elif in_wires is not None or out_wires is not None:
            # Handle explicit in_wires and out_wires
            if ports is None:
                ports = {}
            # Create wire dictionary for direct access
            self.wires = WireDict()
            from .module import Wire
            
            # Handle input wires
            if in_wires is not None:
                for name, dtype in in_wires.items():
                    # Create input wires with explicit direction
                    self.wires._wires[name] = Wire(dtype, 'input')
                    # For backward compatibility, we still create ports
                    from .module import Port
                    ports[name] = Port(dtype)
                # Set in_wires attribute for backward compatibility
                class InWires:
                    def __init__(self, ext_module):
                        self.ext_module = ext_module
                    
                    def __getitem__(self, key):
                        return self.ext_module.wires[key]
                    
                    def __setitem__(self, key, value):
                        # Create a wire assignment expression
                        # Access the wire object directly, not through __getitem__ which calls get_value()
                        wire_obj = self.ext_module.wires._wires.get(key)
                        if wire_obj is None:
                            # If wire doesn't exist, create it as an input wire
                            from .module import Wire
                            from ...builder import Singleton
                            # Try to infer the dtype from the value
                            dtype = getattr(value, 'dtype', None) if value is not None else None
                            wire_obj = Wire(dtype, 'input')
                            self.ext_module.wires._wires[key] = wire_obj
                        elif wire_obj.direction == 'output':
                            raise ValueError(f"Cannot assign to output wire '{key}'")
                        elif wire_obj.direction is None:
                            # Set undirected wire to input
                            wire_obj.direction = 'input'
                        
                        # For input wires, we want to assign the value to the wire
                        # wire_assign expects (wire, value) where wire is the wire being assigned to
                        wire_assign(wire_obj, value)
                        self.ext_module.wires[key] = value
                
                self.in_wires = InWires(self)
                    
            # Handle output wires
            if out_wires is not None:
                for name, dtype in out_wires.items():
                    # Create output wires with explicit direction
                    self.wires._wires[name] = Wire(dtype, 'output')
                    # For backward compatibility, we still create ports
                    from .module import Port
                    ports[name] = Port(dtype)
                # Set out_wires attribute for backward compatibility
                class OutWires:
                    def __init__(self, ext_module):
                        self.ext_module = ext_module
                    
                    def __getitem__(self, key):
                        return self.ext_module.wires[key]
                    
                    def __setitem__(self, key, value):
                        # Create a wire assignment expression
                        wire_assign(self.ext_module.wires[key], value)
                        self.ext_module.wires[key] = value
                        
                self.out_wires = OutWires(self)
        
        # Initialize as regular module
        super().__init__(ports or {}, no_arbiter)
        
        # Add attribute to mark as external
        self._attrs[Module.ATTR_EXTERNAL] = True
        
        # Handle wire connections passed as keyword arguments
        if wire_connections:
            for wire_name, value in wire_connections.items():
                # Check if this is an input wire
                if hasattr(self, 'wires') and wire_name in self.wires:
                    wire_obj = self.wires._wires[wire_name]
                    if wire_obj.direction == 'input' or wire_obj.direction is None:
                        # For input wires, create a wire assignment
                        from ..expr import wire_assign
                        wire_assign(wire_obj, value)
                        # Also set the wire value for direct access
                        self.wires[wire_name] = value
                    else:
                        raise ValueError(f"Cannot assign to output wire '{wire_name}' during initialization")
                else:
                    # If wire doesn't exist, create it as an input wire
                    from .module import Wire
                    from ..expr import wire_assign
                    # Try to infer the dtype from the value
                    dtype = getattr(value, 'dtype', None) if value is not None else None
                    wire_obj = Wire(dtype, 'input')
                    if not hasattr(self, 'wires'):
                        self.wires = WireDict()
                    self.wires._wires[wire_name] = wire_obj
                    wire_assign(wire_obj, value)
                    self.wires[wire_name] = value
        
    def __setitem__(self, key, value):
        '''Allow assignment to wires using bracket notation.'''
        if hasattr(self, 'wires') and key in self.wires:
            self.wires[key].assign(value)
        else:
            raise KeyError(f"Wire '{key}' not found")
            
    def __getitem__(self, key):
        '''Allow access to wires using bracket notation.'''
        if hasattr(self, 'wires') and key in self.wires:
            return self.wires[key].get_value()
        else:
            raise KeyError(f"Wire '{key}' not found")
        
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
        
        from ...builder import Singleton
        Singleton.repr_ident = 2
        body = self.body.__repr__() if self.body else ''
        ext = self._dump_externals()
        return f'''{ext}{ext_info}  {attrs}
  {var_id} = external_module {self.name} {ports}{{
{body}
  }}'''