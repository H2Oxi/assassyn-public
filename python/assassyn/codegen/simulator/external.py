"""Simulator helpers for working with ExternalSV modules."""

from __future__ import annotations

from typing import Dict, Iterable, Set, Tuple

from ...analysis import expr_externally_used
from ...ir.block import Block
from ...ir.expr import Expr, WireRead
from ...ir.module import Downstream, Module
from ...ir.module.external import ExternalSV
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

    def visitor(node):  # Helper for _walk_block
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


def gather_expr_validities(
    sys,
) -> Tuple[Set[Expr], Dict[Module, Set[Expr]]]:  # type: ignore[type-arg]
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


def is_stub_external(module: Module) -> bool:
    """Return True if the ExternalSV module has no synthesized body."""

    if not isinstance(module, ExternalSV):
        return False

    body = getattr(module, "body", None)
    body_insts = getattr(body, "body", []) if body is not None else []
    return not body_insts


__all__ = [
    "collect_external_wire_reads",
    "collect_module_value_exposures",
    "external_handle_field",
    "gather_expr_validities",
    "is_stub_external",
]
