"""Stimulus helpers for assembling reusable driver patterns."""

from __future__ import annotations

from collections.abc import Sequence as ABCSequence
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence, Union

from ..frontend import RegArray, UInt
from ..ir.dtype import DType
from ..ir.value import Value


PayloadValue = Union[int, Value, Callable[[Value], Value]]
CycleKey = Union[int, Sequence[int], range]


def _as_cycle_set(selector: CycleKey) -> Iterable[int]:
    if isinstance(selector, range):
        return selector
    if isinstance(selector, ABCSequence):
        return selector
    return (int(selector),)


class StimulusSignal:
    """Describe how a single signal evolves across simulation cycles."""

    def __init__(self, name: str, dtype: DType):
        self.name = name
        self.dtype = dtype
        self._cases: Dict[int, PayloadValue] = {}
        self._default: Optional[PayloadValue] = dtype(0)

    def set(self, cycle: CycleKey, value: PayloadValue) -> "StimulusSignal":
        """Assign value to one or multiple cycles."""
        for item in _as_cycle_set(cycle):
            self._cases[int(item)] = value
        return self

    def sequence(self, values: Sequence[PayloadValue], *, start: int = 0) -> "StimulusSignal":
        """Assign a consecutive sequence beginning at start."""
        for offset, value in enumerate(values):
            self._cases[start + offset] = value
        return self

    def case_map(self, mapping: Mapping[int, PayloadValue], *, default: Optional[PayloadValue] = None) -> "StimulusSignal":
        """Apply case-style mapping from cycle to value."""
        for cycle, value in mapping.items():
            self.set(int(cycle), value)
        if default is not None:
            self.default(default)
        return self

    def repeat(self, values: Sequence[PayloadValue], *, start: int = 0, period: int = 1) -> "StimulusSignal":
        """Repeat a pattern with a fixed period."""
        if period <= 0:
            raise ValueError("period must be positive")
        for offset, value in enumerate(values):
            self._cases[start + offset * period] = value
        return self

    def default(self, value: PayloadValue) -> "StimulusSignal":
        """Set the fallback value for unmatched cycles."""
        self._default = value
        return self

    def build(self, counter: Value, counter_dtype: DType) -> Value:
        """Materialize the signal as a case-expression driven by counter."""
        if self._default is None:
            raise ValueError(f"Signal '{self.name}' requires a default value.")

        cases: Dict[Optional[Value], Value] = {}
        for cycle, value in self._cases.items():
            cases[counter_dtype(cycle)] = self._coerce_value(value, counter)
        cases[None] = self._coerce_value(self._default, counter)
        return counter.case(cases)

    def _coerce_value(self, value: PayloadValue, counter: Value) -> Value:
        if callable(value):
            result = value(counter)
            if not isinstance(result, Value):
                raise TypeError("Callable payload must return a Value.")
            return result
        if isinstance(value, Value):
            return value
        if isinstance(value, int):
            return self.dtype(value)
        raise TypeError(f"Unsupported payload type {type(value)} for signal '{self.name}'.")


@dataclass(frozen=True)
class StimulusBinding:
    """Map a signal set into a target callable."""

    target: Callable[..., None]
    mapping: Mapping[str, str]


class StimulusTimeline:
    """Container coordinating multiple signals sharing a counter."""

    def __init__(self, counter_dtype: Optional[DType] = None, *, step: int = 1, init: int = 0):
        self.counter_dtype: DType = counter_dtype if counter_dtype is not None else UInt(32)
        self.step = step
        self.init = init
        self._signals: MutableMapping[str, StimulusSignal] = {}
        self._cache: Dict[Value, Dict[str, Value]] = {}
        self._counter: Optional[RegArray] = None

    def signal(self, name: str, dtype: DType) -> StimulusSignal:
        if name in self._signals:
            return self._signals[name]
        self._signals[name] = StimulusSignal(name, dtype)
        return self._signals[name]

    def build_counter(self) -> RegArray:
        if self._counter is None:
            self._counter = RegArray(self.counter_dtype, 1)
        return self._counter

    def values(self, counter: Value) -> Dict[str, Value]:
        cached = self._cache.get(counter)
        if cached is not None:
            return cached
        resolved = {name: signal.build(counter, self.counter_dtype) for name, signal in self._signals.items()}
        self._cache[counter] = resolved
        return resolved


class StimulusDriver:
    """Bind timeline signals to module call-sites."""

    def __init__(self, timeline: StimulusTimeline):
        self.timeline = timeline
        self._bindings: list[StimulusBinding] = []

    def bind(self, target: Callable[..., None], **signal_map: str) -> "StimulusDriver":
        binding = StimulusBinding(target=target, mapping=dict(signal_map))
        self._bindings.append(binding)
        return self

    def drive(self, counter: Value) -> None:
        payload = self.timeline.values(counter)
        for binding in self._bindings:
            kwargs = {port: payload[signal_name] for port, signal_name in binding.mapping.items()}
            binding.target(**kwargs)
