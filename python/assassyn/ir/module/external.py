'''External module implementation for integrating SystemVerilog modules.'''

from __future__ import annotations

# pylint: disable=duplicate-code

import os
from dataclasses import dataclass
from typing import Dict, Optional

from ...builder import Singleton
from ..dtype import DType
from ..expr import wire_assign, wire_read
from .downstream import Downstream
from .module import Module, Wire


@dataclass(frozen=True)
class _WireAnnotation:
    '''Descriptor returned by `Input[...]`/`Output[...]` annotations.'''

    direction: str
    dtype: DType


@dataclass(frozen=True)
class _ExternalConfig:
    '''Resolved configuration for an `ExternalSV` subclass.'''

    file_path: Optional[str]
    module_name: Optional[str]
    has_clock: bool
    has_reset: bool
    no_arbiter: bool
    in_wires: Dict[str, DType]
    out_wires: Dict[str, DType]


class Input:
    '''Annotation helper for declaring ExternalSV inputs.'''

    def __class_getitem__(cls, dtype: DType) -> _WireAnnotation:
        if not isinstance(dtype, DType):
            raise TypeError("Input[...] expects an assassyn dtype instance")
        return _WireAnnotation('input', dtype)


class Output:
    '''Annotation helper for declaring ExternalSV outputs.'''

    def __class_getitem__(cls, dtype: DType) -> _WireAnnotation:
        if not isinstance(dtype, DType):
            raise TypeError("Output[...] expects an assassyn dtype instance")
        return _WireAnnotation('output', dtype)


def _ensure_property(cls, name: str, direction: str):
    '''Install attribute helpers for accessing external wires.'''
    if hasattr(cls, name):
        return
    if direction == 'output':
        def getter(self, wire_name=name):
            return self.out_wires[wire_name]
        setattr(cls, name, property(getter))
    else:
        def getter(self, wire_name=name):
            return self.in_wires[wire_name]

        def setter(self, value, wire_name=name):
            self.in_wires[wire_name] = value
        setattr(cls, name, property(getter, setter))


def external(cls):
    '''Decorator that enables the simplified ExternalSV frontend.'''
    if not issubclass(cls, ExternalSV):
        raise TypeError("@external can only decorate ExternalSV subclasses")

    annotations = getattr(cls, '__annotations__', {})
    in_wires: Dict[str, DType] = {}
    out_wires: Dict[str, DType] = {}

    for name, annotation in annotations.items():
        if isinstance(annotation, _WireAnnotation):
            if annotation.direction == 'input':
                in_wires[name] = annotation.dtype
            else:
                out_wires[name] = annotation.dtype
            _ensure_property(cls, name, annotation.direction)

    file_path = getattr(cls, '__source__', None)
    module_name = getattr(cls, '__module_name__', None)
    has_clock = getattr(cls, '__has_clock__', False)
    has_reset = getattr(cls, '__has_reset__', False)
    no_arbiter = getattr(cls, '__no_arbiter__', False)

    cls.__external_config__ = _ExternalConfig(
        file_path=file_path,
        module_name=module_name,
        has_clock=bool(has_clock),
        has_reset=bool(has_reset),
        no_arbiter=bool(no_arbiter),
        in_wires=in_wires,
        out_wires=out_wires,
    )
    return cls


class DirectionalWires:
    """Adapter exposing directional wire access consistent with the simplified API."""

    def __init__(self, ext_module, direction):
        self._module = ext_module
        self._direction = direction

    def _get_wire(self, key):
        wire = self._module.wires.get(key)
        if wire is None:
            raise KeyError(f"Wire '{key}' not found")
        if wire.direction != self._direction:
            raise ValueError(f"Wire '{key}' is not an {self._direction} wire")
        return wire

    def __contains__(self, key):
        wire = self._module.wires.get(key)
        return wire is not None and wire.direction == self._direction

    def __iter__(self):
        return iter(self.keys())

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
        """Return the names of wires that match this adapter's direction."""
        return [
            name for name, wire in self._module.wires.items()
            if wire.direction == self._direction
        ]


class ExternalSV(Downstream):
    '''An external block implemented in SystemVerilog.'''

    __external_config__: _ExternalConfig | None = None

    # pylint: disable=too-many-arguments,too-many-locals,too-many-branches,too-many-statements
    def __init__(
        self,
        file_path=None,
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
        config = getattr(type(self), '__external_config__', None)

        if config:
            in_wires = in_wires or config.in_wires
            out_wires = out_wires or config.out_wires
            file_path = file_path or config.file_path
            module_name = module_name or config.module_name
            has_clock = has_clock or config.has_clock
            has_reset = has_reset or config.has_reset
            no_arbiter = no_arbiter or config.no_arbiter

        if file_path is None:
            raise ValueError(
                "ExternalSV requires a 'file_path'. "
                "Provide it explicitly or use the @external decorator with '__source__'."
            )

        super().__init__()

        # Store external file information
        self.file_path = file_path

        self.external_module_name = module_name or type(self).__name__
        self.has_clock = has_clock
        self.has_reset = has_reset

        self._attrs = {}
        if no_arbiter:
            self._attrs[Module.ATTR_DISABLE_ARBITER] = True
        self._attrs[Module.ATTR_EXTERNAL] = True

        self._wires = {}

        def _register_wire(name, dtype, direction):
            wire = Wire(dtype, direction, self)
            wire.name = name
            self._wires[name] = wire

        if in_wires:
            for wire_name, dtype in in_wires.items():
                _register_wire(wire_name, dtype, 'input')
        if out_wires:
            for wire_name, dtype in out_wires.items():
                _register_wire(wire_name, dtype, 'output')

        self.in_wires = DirectionalWires(self, 'input')
        self.out_wires = DirectionalWires(self, 'output')

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

    @property
    def wires(self):
        """Expose declared wires keyed by name for helper adapters."""
        return self._wires

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

        outputs = [self.out_wires[name] for name in self.out_wires]
        if not outputs:
            return None
        if len(outputs) == 1:
            return outputs[0]
        return tuple(outputs)

    def __repr__(self):
        '''String representation of the external module.'''
        wires = '\n    '.join(
            f"{name}: {wire}" for name, wire in self._wires.items()
        )
        wire_lines = f'{{\n    {wires}\n  }} ' if wires else ''
        attrs = ', '.join(
            f'{Module.MODULE_ATTR_STR[i]}: {j}' for i, j in self._attrs.items()
        )
        attrs = f'#[{attrs}] ' if attrs else ''
        var_id = self.as_operand()

        ext_info = f'  // External file: {self.file_path}\n'
        ext_info += f'  // External module name: {self.external_module_name}\n'

        Singleton.repr_ident = 2
        body = self.body.__repr__() if self.body else ''
        ext = self._dump_externals()
        return f'''{ext}{ext_info}  {attrs}
  {var_id} = external_module {self.name} {wire_lines}{{
{body}
  }}'''
