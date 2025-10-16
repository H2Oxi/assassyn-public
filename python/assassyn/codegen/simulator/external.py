"""Simulator helpers for working with ExternalSV modules."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, DefaultDict, Dict, Iterable, List, Set, Tuple

from ...analysis import expr_externally_used
from ...ir.block import Block
from ...ir.expr import Expr, WireAssign, WireRead
from ...ir.module import Downstream, Module
from ...ir.module.external import ExternalSV
from ...ir.module.module import Wire
from ...ir.visitor import Visitor
from ...utils import namify, unwrap_operand
from .utils import dtype_to_rust_type


def external_handle_field(module_name: str) -> str:
    """Return the simulator struct field name for an ExternalSV handle."""

    return f"{namify(module_name)}_ffi"


def _get_wire_owner(wire):
    """Return the owner module of a wire (parent or module attribute)."""
    return getattr(wire, "parent", None) or getattr(wire, "module", None)


class _ExternalWireReadCollector(Visitor):
    """Collect ``WireRead`` expressions that observe ExternalSV outputs."""

    def __init__(self):
        super().__init__()
        self.reads: Set[Expr] = set()

    def visit_expr(self, expr: Expr) -> None:
        if isinstance(expr, WireRead):
            owner = _get_wire_owner(expr.wire)
            if isinstance(owner, ExternalSV):
                self.reads.add(expr)


class _ModuleValueExposureCollector(Visitor):
    """Collect expressions that need simulator-side caching."""

    def __init__(self):
        super().__init__()
        self.exprs: Set[Expr] = set()

    def visit_expr(self, expr: Expr) -> None:
        if expr_externally_used(expr, True):
            self.exprs.add(expr)


def _assignment_handled_by_producer(
    value_expr: object,
    external_value_assignments: Dict[tuple, List[Tuple[ExternalSV, Wire]]],
) -> bool:
    """Return True if the producer module already emits assignments for this value.

    This function accepts either an Expr or an Operand wrapping an Expr.
    Operand's __getattr__ allows direct attribute access without explicit unwrapping.
    """
    # Check if it's an Expr (directly or wrapped in Operand)
    # For Operand, isinstance check fails but __getattr__ forwards to wrapped value
    actual_expr = unwrap_operand(value_expr)
    if not isinstance(actual_expr, Expr):
        return False

    # Use value_expr directly - Operand.__getattr__ forwards attribute access
    parent_block = value_expr.parent
    producer_module = getattr(parent_block, "module", None)
    if producer_module is None:
        return False
    key = (producer_module, namify(value_expr.as_operand()))
    return key in external_value_assignments


def collect_external_wire_reads(module: Module) -> Set[Expr]:
    """Collect WireRead expressions that observe ExternalSV outputs."""

    body = getattr(module, "body", None)
    if body is None:
        return set()
    collector = _ExternalWireReadCollector()
    collector.visit_block(body)
    return collector.reads


def collect_module_value_exposures(module: Module) -> Set[Expr]:
    """Collect expressions that require simulator-side caching for a module."""

    body = getattr(module, "body", None)
    if body is None:
        return set()
    collector = _ModuleValueExposureCollector()
    collector.visit_block(body)
    return collector.exprs


def iter_wire_assignments(root: Block) -> Iterable[WireAssign]:
    """Yield ``WireAssign`` nodes from nested blocks."""

    stack = [root]
    while stack:
        block = stack.pop()
        for elem in getattr(block, "body", []):
            if isinstance(elem, Block):
                stack.append(elem)
            elif isinstance(elem, WireAssign):
                yield elem


def collect_external_value_assignments(sys) -> DefaultDict[tuple, List[Tuple[ExternalSV, Wire]]]:
    """Precompute external input assignments keyed by producing expression."""

    assignments: DefaultDict[tuple, List[Tuple[ExternalSV, Wire]]] = defaultdict(list)

    for module in getattr(sys, "downstreams", []):
        if not isinstance(module, ExternalSV):
            continue
        body = getattr(module, "body", None)
        if body is None:
            continue
        for assignment in iter_wire_assignments(body):
            value = unwrap_operand(assignment.value)
            if not isinstance(value, Expr):
                continue
            parent_block = getattr(value, "parent", None)
            producer_module = getattr(parent_block, "module", None)
            if producer_module is None:
                continue
            value_id = namify(value.as_operand())
            assignments[(producer_module, value_id)].append((module, assignment.wire))
    return assignments


def lookup_external_port(external_specs, module_name: str, wire_name: str, direction: str):
    """Return the FFI port spec for the given external wire, if available."""

    spec = external_specs.get(module_name)
    if spec is None:
        return None
    target = namify(wire_name)
    ports = spec.inputs if direction == "input" else spec.outputs
    for port in ports:
        if port.name == target:
            return port
    return None


def codegen_external_wire_assign(
    node: WireAssign,
    *,
    external_specs: Dict[str, Any],
    external_value_assignments: Dict[tuple, List[Tuple[ExternalSV, Wire]]],
    value_code: str,
) -> str | None:
    """Produce simulator code for driving an ExternalSV input wire."""

    wire = node.wire
    owner = _get_wire_owner(wire)
    wire_name = getattr(wire, "name", None)
    if not isinstance(owner, ExternalSV) or not wire_name:
        return None

    # Pass node.value directly - _assignment_handled_by_producer handles Operand unwrapping
    if _assignment_handled_by_producer(node.value, external_value_assignments):
        # Assignment handled in producer module to preserve evaluation ordering.
        return ""

    spec = external_specs.get(owner.name)
    if spec is None:
        raise ValueError(f"Missing external FFI spec for module {owner.name}")

    port_spec = lookup_external_port(external_specs, owner.name, wire_name, "input")
    rust_ty = port_spec.rust_type if port_spec is not None else dtype_to_rust_type(wire.dtype)
    handle_field = external_handle_field(owner.name)
    method_suffix = namify(wire_name)

    return (
        f"// External wire assign: {owner.name}.{wire_name}\n"
        f"sim.{handle_field}.set_{method_suffix}("
        f"ValueCastTo::<{rust_ty}>::cast(&{value_code}));"
    )


def codegen_external_wire_read(
    node: WireRead,
    *,
    external_specs: Dict[str, Any],
) -> str | None:
    """Produce simulator code for reading an ExternalSV output wire."""

    wire = node.wire
    owner = _get_wire_owner(wire)
    wire_name = getattr(wire, "name", None)
    if not isinstance(owner, ExternalSV) or not wire_name:
        return None

    spec = external_specs.get(owner.name)
    if spec is None:
        raise ValueError(f"Missing external FFI spec for module {owner.name}")

    handle_field = external_handle_field(owner.name)
    method_suffix = namify(wire_name)
    rust_ty = dtype_to_rust_type(node.dtype)

    eval_line = f"  sim.{handle_field}.eval();\n"

    return (
        "{\n"
        f"{eval_line}  let value = sim.{handle_field}.get_{method_suffix}();\n"
        f"  ValueCastTo::<{rust_ty}>::cast(&value)\n"
        "}"
    )


def gather_expr_validities(sys) -> Tuple[Set[Expr], Dict[Module, Set[Expr]]]:
    """Aggregate expressions whose values must be cached on the simulator."""

    exprs: Set[Expr] = set()
    module_expr_map: Dict[Module, Set[Expr]] = {}

    def record(module: Module, expr: Expr) -> None:
        exprs.add(expr)
        module_expr_map.setdefault(module, set()).add(expr)

    modules: Iterable[Module] = list(sys.modules) + list(sys.downstreams)
    for module in modules:
        if isinstance(module, Downstream):
            for expr in module.externals:
                if isinstance(expr, Expr):
                    record(module, expr)

        for expr in collect_module_value_exposures(module):
            record(module, expr)
        for expr in collect_external_wire_reads(module):
            record(module, expr)

        externals = getattr(module, "externals", None)
        if externals:
            for expr in externals:
                if isinstance(expr, Expr):
                    record(module, expr)

    return exprs, module_expr_map


def has_module_body(module: Module) -> bool:
    """Return True when the module has an elaborated body."""
    body = getattr(module, "body", None)
    return body is not None and bool(getattr(body, "body", []))


def is_stub_external(module: Module) -> bool:
    """Return True if the ExternalSV module has no synthesized body."""

    return isinstance(module, ExternalSV) and not has_module_body(module)


__all__ = [
    "codegen_external_wire_assign",
    "codegen_external_wire_read",
    "collect_external_value_assignments",
    "collect_external_wire_reads",
    "collect_module_value_exposures",
    "external_handle_field",
    "gather_expr_validities",
    "has_module_body",
    "is_stub_external",
    "iter_wire_assignments",
    "lookup_external_port",
]
