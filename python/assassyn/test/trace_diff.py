"""Trace utilities for comparing simulator logs against Spike golden traces.

This module focuses on parsing Spike-generated `.trace` files so that higher
level checkers can diff them against executor-stage logs emitted by the CPU
tests.  The Spike trace format (as produced by `spike --log-commits`) emits
pairs of lines per instruction: one describing the instruction fetch/decoded
information, and another (`core 0: 3 ...`) describing architectural updates.
The helpers below keep only the instruction stream, which is all we need for
trace alignment checks.

Example
-------
>>> SAMPLE = \"\"\"\
... core   0: 0x00010000 (0x000102b7) lui     t0, 0x10
... core   0: 3 0x00010000 (0x000102b7) x5  0x00010000
... core   0: 0x00010004 (0x02c28293) addi    t0, t0, 44
... \"\"\"
>>> trace = SpikeTrace.from_text(SAMPLE)
>>> [(hex(e.pc), hex(e.insn)) for e in trace]  # doctest: +NORMALIZE_WHITESPACE
[('0x10000', '0x102b7'), ('0x10004', '0x2c28293')]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import sys
from typing import Callable, Iterable, Iterator, List, Optional, Sequence

TRACE_LINE_RE = re.compile(
    r"""
    ^
    core\s+(?P<core>\d+):
    \s+
    (?:(?P<commit>3)\s+)?
    0x(?P<pc>[0-9a-fA-F]+)
    \s+\(
    (?P<ins>0x[0-9a-fA-F]+)
    \)
    \s*
    (?P<body>.*)
    $
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class TraceEntry:
    """Single instruction entry parsed from a Spike trace."""

    index: int
    pc: int
    insn: int
    text: str

    def key(self) -> tuple[int, int]:
        """Return (pc, insn) tuple for equality comparisons."""
        return self.pc, self.insn


class SpikeTrace(Sequence[TraceEntry]):
    """Immutable container for Spike trace entries."""

    def __init__(self, entries: Iterable[TraceEntry]):
        self._entries: List[TraceEntry] = list(entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, idx):
        return self._entries[idx]

    def as_pairs(self) -> List[tuple[int, int]]:
        """Return the trace as [(pc, insn), ...] for quick comparisons."""
        return [entry.key() for entry in self._entries]

    @classmethod
    def from_file(cls, path: str | Path, *, drop_commits: bool = True) -> "SpikeTrace":
        """Load a trace file from disk."""
        text = Path(path).read_text(encoding="utf-8")
        return cls(parse_spike_trace(text, drop_commits=drop_commits))

    @classmethod
    def from_text(cls, text: str, *, drop_commits: bool = True) -> "SpikeTrace":
        """Build a trace from an in-memory string (useful for tests)."""
        return cls(parse_spike_trace(text, drop_commits=drop_commits))


def parse_spike_trace(text: str, *, drop_commits: bool = True) -> Iterator[TraceEntry]:
    """
    Yield `TraceEntry` objects parsed from Spike's trace log.

    Args:
        text: Raw text content of the `.trace` file.
        drop_commits: When True (default) skip `core N: 3 ...` lines that only
            describe write-back information.  Set to False to keep them.
    """
    for idx, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = TRACE_LINE_RE.match(line)
        if not match:
            raise ValueError(f"Unrecognized trace line #{idx}: {raw}")
        if match.group("commit") and drop_commits:
            continue
        pc = int(match.group("pc"), 16)
        insn = int(match.group("ins"), 16)
        body = match.group("body").strip()
        yield TraceEntry(index=idx, pc=pc, insn=insn, text=body)


EXECUTOR_FETCH_RE = re.compile(
    r"""
    ^\s*@line:(?P<log_line>\d+)
    [^\n]*?
    Cycle\s+@(?P<cycle>[0-9.]+)
    [^\n]*?
    \[executor\]\s+fetch_addr:\s+0x(?P<pc>[0-9a-fA-F]+)
    \s+ins:\s+0x(?P<ins>[0-9a-fA-F]+)
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class ExecutorLogEntry:
    """Single executor log entry describing an instruction issue."""

    index: int  # zero-based position within the log file
    pc: int
    insn: int
    cycle: Optional[float]
    log_line: Optional[int]
    raw: str

    def key(self) -> tuple[int, int]:
        return self.pc, self.insn


def parse_executor_log(text: str) -> List[ExecutorLogEntry]:
    """Extract executor instruction entries from simulator output."""
    entries: List[ExecutorLogEntry] = []
    for idx, raw in enumerate(text.splitlines()):
        match = EXECUTOR_FETCH_RE.search(raw)
        if not match:
            continue
        cycle_raw = match.group("cycle")
        cycle = float(cycle_raw) if cycle_raw else None
        log_line_raw = match.group("log_line")
        log_line = int(log_line_raw) if log_line_raw is not None else None
        pc = int(match.group("pc"), 16)
        insn = int(match.group("ins"), 16)
        entries.append(
            ExecutorLogEntry(
                index=idx,
                pc=pc,
                insn=insn,
                cycle=cycle,
                log_line=log_line,
                raw=raw.strip(),
            )
        )
    return entries


@dataclass(frozen=True)
class TraceDiffStats:
    matched: int
    expected: int
    actual: int
    issues: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.issues


class ExecutorTraceChecker:
    """Compare executor log entries against a Spike trace."""

    def __init__(
        self,
        golden: SpikeTrace,
        *,
        strict_length: bool = True,
        failure_mode: str = "error",
        warn_handler: Optional[Callable[[str], None]] = None,
    ):
        self._golden = golden
        self._strict_length = strict_length
        failure_mode = failure_mode.lower()
        if failure_mode not in {"error", "warn"}:
            raise ValueError("failure_mode must be 'error' or 'warn'")
        self._failure_mode = failure_mode
        self._warn_handler = warn_handler if warn_handler is not None else self._default_warn_handler

    def check(self, log_text: str) -> TraceDiffStats:
        entries = parse_executor_log(log_text)
        return self.compare(entries)

    def compare(self, entries: Sequence[ExecutorLogEntry]) -> TraceDiffStats:
        expected_pairs = self._golden.as_pairs()
        actual_pairs = [entry.key() for entry in entries]
        limit = min(len(expected_pairs), len(actual_pairs))
        issues: List[str] = []
        matched = limit
        for idx in range(limit):
            if actual_pairs[idx] == expected_pairs[idx]:
                continue
            matched = idx
            exp_pc, exp_insn = expected_pairs[idx]
            actual = entries[idx]
            exp_pc_str = f"0x{exp_pc:08x}"
            exp_insn_str = f"0x{exp_insn:08x}"
            act_pc_str = f"0x{actual.pc:08x}"
            act_insn_str = f"0x{actual.insn:08x}"
            log_loc = actual.log_line if actual.log_line is not None else actual.index
            issue = (
                f"Executor trace mismatch at #{idx}: "
                f"expected pc={exp_pc_str} ins={exp_insn_str}, "
                f"got pc={act_pc_str} ins={act_insn_str} "
                f"(log line {log_loc}).\n"
                f"Log record: {actual.raw}"
            )
            if self._handle_issue(issue, issues):
                break

        expected_len = len(expected_pairs)
        actual_len = len(actual_pairs)
        if self._strict_length and len(actual_pairs) != len(expected_pairs):
            if actual_len < expected_len:
                next_pc, next_insn = expected_pairs[actual_len]
                issue = (
                    "Executor trace ended early: "
                    f"actual={actual_len}, expected={expected_len}. "
                    f"Next expected entry pc=0x{next_pc:08x} ins=0x{next_insn:08x}."
                )
            else:
                extra = entries[expected_len]
                loc = extra.log_line if extra.log_line is not None else extra.index
                issue = (
                    "Executor trace longer than golden trace: "
                    f"actual={actual_len}, expected={expected_len}. "
                    f"First extra entry pc=0x{extra.pc:08x} ins=0x{extra.insn:08x} "
                    f"(log line {loc})."
                )
            self._handle_issue(issue, issues)

        return TraceDiffStats(
            matched=matched,
            expected=expected_len,
            actual=actual_len,
            issues=issues,
        )

    def _handle_issue(self, message: str, issues: List[str]) -> bool:
        if self._failure_mode == "error":
            raise AssertionError(message)
        issues.append(message)
        self._warn_handler(message)
        return True

    @staticmethod
    def _default_warn_handler(message: str) -> None:
        first_line = message.splitlines()[0]
        print(f"[trace-diff] WARN: {first_line}", file=sys.stderr)


def _self_test() -> None:
    """Minimal internal check to guard against format regressions."""
    sample = (
        "core   0: 0x00010000 (0x000102b7) lui     t0, 0x10\n"
        "core   0: 3 0x00010000 (0x000102b7) x5  0x00010000\n"
        "core   0: 0x00010004 (0x02c28293) addi    t0, t0, 44\n"
    )
    trace = SpikeTrace.from_text(sample)
    assert len(trace) == 2, trace
    assert trace.as_pairs()[0] == (0x00010000, 0x000102B7)
    log_sample = (
        "@line:253   Cycle @15.00: [E]\t[executor] fetch_addr: 0x00010000 ins: 0x000102b7\n"
        "@line:260   Cycle @16.00: [E]\t[executor] fetch_addr: 0x00010004 ins: 0x02c28293\n"
    )
    log_entries = parse_executor_log(log_sample)
    assert len(log_entries) == 2
    checker = ExecutorTraceChecker(trace)
    checker.compare(log_entries)
    try:
        bad_log = log_sample.replace("0x02c28293", "0xdeadbeef")
        checker.check(bad_log)
    except AssertionError:
        pass
    else:
        raise AssertionError("ExecutorTraceChecker failed to detect mismatch")
    warn_checker = ExecutorTraceChecker(trace, failure_mode="warn")
    stats = warn_checker.check(bad_log)
    assert stats.issues, "warn mode should surface issues"


__all__ = [
    "TraceEntry",
    "SpikeTrace",
    "parse_spike_trace",
    "ExecutorLogEntry",
    "parse_executor_log",
    "TraceDiffStats",
    "ExecutorTraceChecker",
]


if __name__ == "__main__":
    _self_test()
