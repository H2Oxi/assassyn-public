"""Utilities for parsing simulator logs and validating against reference models."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Callable, Dict, Iterable, Iterator, Mapping, Optional, Sequence, Tuple


LineIterator = Iterable[str]
ExtractedLine = Tuple[int, str]
Hook = Callable[["LogRecord"], None]


def _auto_cast(_key: str, value: str) -> Any:
    """Attempt to convert value to int when possible, otherwise keep original string."""
    try:
        return int(value, 0)
    except ValueError:
        return value


@dataclass
class LogRecord:
    """Structured log entry after parsing."""

    index: int
    line: str
    data: Dict[str, Any]
    meta: Dict[str, Any] = field(default_factory=dict)

    def require(self, *fields: str) -> None:
        """Ensure required fields are present."""
        missing = [field for field in fields if field not in self.data]
        if missing:
            raise AssertionError(f"Missing fields {missing} in record #{self.index}: {self.line}")


class PrefixExtractor:
    """Extract lines that start with (or contain) a marker."""

    def __init__(self, marker: str, *, mode: str = "startswith", strip: bool = True, drop: bool = True):
        """
        Args:
            marker: Prefix/substring used to identify target lines.
            mode: "startswith" (default) or "contains".
            strip: Whether to strip whitespace before matching.
            drop: Remove the marker from the returned line when possible.
        """
        assert mode in {"startswith", "contains"}, "mode must be either 'startswith' or 'contains'"
        self.marker = marker
        self.mode = mode
        self.strip = strip
        self.drop = drop

    def __call__(self, lines: LineIterator) -> Iterator[ExtractedLine]:
        processed = (line.strip() if self.strip else line for line in lines)
        for index, raw in enumerate(processed):
            candidate = raw
            matched = False
            if self.mode == "startswith":
                matched = candidate.startswith(self.marker)
            elif self.mode == "contains":
                matched = self.marker in candidate
            if not matched:
                continue
            if self.drop:
                if self.mode == "startswith" and candidate.startswith(self.marker):
                    candidate = candidate[len(self.marker) :].lstrip()
                elif self.mode == "contains":
                    _, _, after = candidate.partition(self.marker)
                    candidate = after.lstrip()
            yield index, candidate


class RegexExtractor:
    """Extract lines matching a regex pattern."""

    def __init__(self, pattern: str, *, flags: int = 0):
        self.pattern = re.compile(pattern, flags)

    def __call__(self, lines: LineIterator) -> Iterator[ExtractedLine]:
        for index, line in enumerate(lines):
            match = self.pattern.search(line)
            if match:
                yield index, line


class KeyValueParser:
    """Parse key:value pairs separated by delimiter (default: '|')."""

    def __init__(
        self,
        *,
        pair_sep: str = "|",
        kv_sep: str = ":",
        value_cast: Optional[Callable[[str, str], Any]] = None,
        key_map: Optional[Mapping[str, str]] = None,
    ):
        self.pair_sep = pair_sep
        self.kv_sep = kv_sep
        self.value_cast = value_cast or _auto_cast
        self.key_map = dict(key_map) if key_map else None

    def __call__(self, line: str, index: int) -> LogRecord:
        data: Dict[str, Any] = {}
        for token in line.split(self.pair_sep):
            token = token.strip()
            if not token or self.kv_sep not in token:
                continue
            key, value = token.split(self.kv_sep, 1)
            key = key.strip()
            value = value.strip()
            mapped_key = self.key_map.get(key, key) if self.key_map else key
            data[mapped_key] = self.value_cast(mapped_key, value)
        return LogRecord(index=index, line=line, data=data)


class RegexParser:
    """Parse a line with named regex groups into structured data."""

    def __init__(self, pattern: str, *, flags: int = 0, value_cast: Optional[Callable[[str, str], Any]] = None):
        self.pattern = re.compile(pattern, flags)
        self.value_cast = value_cast or _auto_cast

    def __call__(self, line: str, index: int) -> LogRecord:
        match = self.pattern.search(line)
        if not match:
            raise AssertionError(f"Line #{index} does not match pattern: {line}")
        data = {key: self.value_cast(key, value) for key, value in match.groupdict().items()}
        return LogRecord(index=index, line=line, data=data, meta={"match": match})


class LogChecker:
    """Coordinator that glues extractor, parser, hooks and summary assertions."""

    def __init__(self, extractor: Callable[[LineIterator], Iterable[ExtractedLine]], parser: Callable[[str, int], LogRecord]):
        self.extractor = extractor
        self.parser = parser
        self._hooks: List[Hook] = []
        self._records: List[LogRecord] = []
        self._expected_count: Optional[int] = None
        self._min_count: Optional[int] = None
        self._summary: Dict[str, Any] = {}

    @property
    def records(self) -> Sequence[LogRecord]:
        return self._records

    @property
    def summary(self) -> Mapping[str, Any]:
        return self._summary

    def add_hook(self, hook: Hook) -> "LogChecker":
        self._hooks.append(hook)
        return self

    def expect_count(self, expected: int) -> "LogChecker":
        self._expected_count = expected
        return self

    def expect_at_least(self, minimum: int) -> "LogChecker":
        self._min_count = minimum
        return self

    def collect(self, raw: str) -> "LogChecker":
        lines = raw.splitlines()
        for index, line in self.extractor(lines):
            record = self.parser(line, index)
            self._records.append(record)
            for hook in self._hooks:
                hook(record)
        count = len(self._records)
        self._summary["count"] = count
        if self._expected_count is not None and count != self._expected_count:
            raise AssertionError(f"Record count mismatch: got {count}, expected {self._expected_count}")
        if self._min_count is not None and count < self._min_count:
            raise AssertionError(f"Record count too small: got {count}, expected >= {self._min_count}")
        return self


class ReferenceHook:
    """Compare parsed records with a reference model."""

    def __init__(
        self,
        model: Callable[..., Any],
        *,
        inputs: Sequence[str],
        outputs: Sequence[str],
        comparator: Optional[Callable[[Mapping[str, Any], Mapping[str, Any], LogRecord], None]] = None,
        build_expected: Optional[Callable[[Any], Mapping[str, Any]]] = None,
        name: str = "reference",
    ):
        self.model = model
        self.inputs = list(inputs)
        self.outputs = list(outputs)
        self.comparator = comparator or self._default_compare
        self.build_expected = build_expected or self._wrap_outputs
        self.name = name

    def __call__(self, record: LogRecord) -> None:
        record.require(*self.inputs, *self.outputs)
        inputs = [record.data[field] for field in self.inputs]
        model_result = self.model(*inputs)
        expected = self.build_expected(model_result)
        missing = [field for field in self.outputs if field not in expected]
        if missing:
            raise AssertionError(f"{self.name} missing expected fields {missing} (record #{record.index})")
        current = {field: record.data[field] for field in self.outputs}
        self.comparator(current, expected, record)

    def _wrap_outputs(self, model_result: Any) -> Mapping[str, Any]:
        if isinstance(model_result, Mapping):
            return model_result
        if len(self.outputs) == 1:
            return {self.outputs[0]: model_result}
        raise TypeError(
            f"{self.name} expected mapping result for multiple outputs {self.outputs}, got {type(model_result).__name__}"
        )

    @staticmethod
    def _default_compare(current: Mapping[str, Any], expected: Mapping[str, Any], record: LogRecord) -> None:
        for key, exp_value in expected.items():
            cur_value = current.get(key)
            if cur_value != exp_value:
                raise AssertionError(
                    f"Record #{record.index} field '{key}' mismatch: got {cur_value}, expected {exp_value}. "
                    f"Line: {record.line}"
                )


def expect_increasing(field: str, *, step: Optional[int] = None) -> Hook:
    """Ensure field values increase monotonically, optionally with fixed step."""
    previous: Dict[str, Any] = {}

    def _hook(record: LogRecord) -> None:
        value = record.data.get(field)
        if value is None:
            raise AssertionError(f"Record #{record.index} missing field '{field}' for monotonic check.")
        if field in previous:
            delta = value - previous[field]
            if step is None:
                if delta <= 0:
                    raise AssertionError(
                        f"Record #{record.index} field '{field}' not increasing: prev={previous[field]}, curr={value}"
                    )
            else:
                if delta != step:
                    raise AssertionError(
                        f"Record #{record.index} field '{field}' step mismatch: prev={previous[field]}, curr={value}, expected step {step}"
                    )
        previous[field] = value

    return _hook


__all__ = [
    "LogRecord",
    "LogChecker",
    "PrefixExtractor",
    "RegexExtractor",
    "KeyValueParser",
    "RegexParser",
    "ReferenceHook",
    "expect_increasing",
]
