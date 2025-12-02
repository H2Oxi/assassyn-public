from assassyn.frontend import *
from assassyn.test import run_test
from assassyn import utils

UNLOCK_AT = 3
STOP_AT = 12
# Waiter should re-evaluate the wait condition for UNLOCK_AT + 2 cycles:
# UNLOCK cycles before driver flips the lock plus one cycle for visibility.


class WaitProbe(Module):

    def __init__(self):
        super().__init__(ports={})

    @module.combinational
    def build(self, gate: Array):
        pre = RegArray(UInt(32), 1)
        post = RegArray(UInt(32), 1)

        log("WAIT_TEST pre {}", pre[0])
        (pre & self)[0] <= pre[0] + UInt(32)(1)

        wait_until(gate[0])

        log("WAIT_TEST post {}", post[0])
        (post & self)[0] <= post[0] + UInt(32)(1)


class Driver(Module):

    def __init__(self):
        super().__init__(ports={})

    @module.combinational
    def build(self, probe: WaitProbe, gate: Array):
        tick = RegArray(UInt(32), 1)
        launched = RegArray(Bits(1), 1)

        (tick & self)[0] <= tick[0] + UInt(32)(1)

        with Condition(~launched[0]):
            probe.async_called()
            (launched & self)[0] <= Bits(1)(1)

        with Condition(tick[0] >= UInt(32)(UNLOCK_AT)):
            (gate & self)[0] <= Bits(1)(1)

        with Condition(tick[0] >= UInt(32)(STOP_AT)):
            finish()


def build_system():
    gate = RegArray(Bits(1), 1)

    waiter = WaitProbe()
    waiter.build(gate)

    driver = Driver()
    driver.build(waiter, gate)


def _parse_cycle(tokens):
    try:
        return utils.parse_simulator_cycle(tokens)
    except Exception:  # pylint: disable=broad-except
        return utils.parse_verilator_cycle(tokens)


def check_output(raw):
    pre_cycles = []
    post_cycles = []
    for line in raw.splitlines():
        if 'WAIT_TEST pre' in line:
            pre_cycles.append(_parse_cycle(line.split()))
        elif 'WAIT_TEST post' in line:
            post_cycles.append(_parse_cycle(line.split()))

    assert len(pre_cycles) >= 2, "wait probe should evaluate predicate multiple times"
    assert len(post_cycles) == 1, "wait probe should complete exactly once"
    assert post_cycles[0] == pre_cycles[-1], "post log must occur on final pre cycle"

    # Ensure the probe kept re-evaluating the wait predicate for multiple cycles.
    total_delay = post_cycles[0] - pre_cycles[0]
    assert total_delay >= UNLOCK_AT - 1, (
        f"wait predicate only held for {total_delay} cycles"
    )


def test_wait_until_ports():
    run_test(
        'wait_until_ports',
        build_system,
        check_output,
        sim_threshold=64,
        idle_threshold=64,
    )


if __name__ == '__main__':
    test_wait_until_ports()
