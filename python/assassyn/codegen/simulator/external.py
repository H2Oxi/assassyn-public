"""Simulator helpers for working with ExternalSV modules."""

from __future__ import annotations

from collections import defaultdict
from typing import DefaultDict, Dict, Iterable, List, Set, Tuple

from ...analysis import expr_externally_used
from ...ir.block import Block
from ...ir.expr import Expr, WireAssign, WireRead
from ...ir.module import Downstream, Module
from ...ir.module.external import ExternalSV
from ...ir.module.module import Wire
from ...utils import namify, unwrap_operand


def external_handle_field(module_name: str) -> str:
    """Return the simulator struct field name for an ExternalSV handle."""

    return f"{namify(module_name)}_ffi"


def _walk_block(block: Block | None, visitor) -> None:
    if block is None or not isinstance(block, Block):
        return
    for item in getattr(block, "body", []):
        visitor(item)


def collect_external_wire_reads(module: Module) -> Set[Expr]:
    """Collect WireRead expressions that observe ExternalSV outputs."""

    reads: Set[Expr] = set()

    def visit_expr(expr):
        if isinstance(expr, WireRead):
            wire = expr.wire
            owner = getattr(wire, "parent", None) or getattr(wire, "module", None)
            if isinstance(owner, ExternalSV):
                reads.add(expr)
        if isinstance(expr, Expr):
            for operand in expr.operands:
                value = unwrap_operand(operand)
                if isinstance(value, Expr):
                    visit_expr(value)

    def visitor(node):
        if isinstance(node, Expr):
            visit_expr(node)
        elif isinstance(node, Block):
            _walk_block(node, visitor)

    _walk_block(getattr(module, "body", None), visitor)
    return reads


def collect_module_value_exposures(module: Module) -> Set[Expr]:
    """Collect expressions that require simulator-side caching for a module."""

    exprs: Set[Expr] = set()

    def visit_expr(expr: Expr) -> None:
        if expr_externally_used(expr, True):
            exprs.add(expr)
        for operand in expr.operands:
            value = unwrap_operand(operand)
            if isinstance(value, Expr):
                visit_expr(value)

    def visitor(node):
        if isinstance(node, Expr):
            visit_expr(node)
        elif isinstance(node, Block):
            _walk_block(node, visitor)

    _walk_block(getattr(module, "body", None), visitor)
    return exprs


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
    body = getattr(module, "body", None)
    return body is not None and bool(getattr(body, "body", []))


def is_stub_external(module: Module) -> bool:
    """Return True if the ExternalSV module has no synthesized body."""

    return isinstance(module, ExternalSV) and not has_module_body(module)


__all__ = [
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
