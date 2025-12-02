from assassyn.frontend import *
from assassyn.test import run_test

class Driver(Module):

    def __init__(self):
        super().__init__(
            ports={} ,
        )

    @module.combinational
    def build(self):
        cnt = RegArray(Int(32), 1)
        (cnt & self)[0] <= cnt[0] + Int(32)(1)
        log('cnt {}', cnt[0])
        wait_until( cnt[0] & Int(32)(1) == Int(32)(0) )
        test = RegArray(Int(32), 1)
        (test & self)[0] <= cnt[0] + Int(32)(1)
        log('test {}', test[0])



def build_top():
    driver = Driver()
    driver.build()

def check(raw):
    cnt_values = []
    test_values = []
    last_cnt = None

    for line in raw.splitlines():
        tokens = line.split()
        if len(tokens) < 2:
            continue
        tag = tokens[-2]
        if tag == 'cnt':
            try:
                value = int(tokens[-1])
            except ValueError:
                continue
            cnt_values.append(value)
            last_cnt = value
        elif tag == 'test':
            try:
                value = int(tokens[-1])
            except ValueError:
                continue
            assert last_cnt is not None, 'missing cnt log before test log'
            assert last_cnt % 2 == 0, f'test write observed when cnt={last_cnt} (expected even)'
            test_values.append(value)

    assert cnt_values, 'no cnt values captured from log'
    assert cnt_values == list(range(cnt_values[-1] + 1)), f'unexpected cnt sequence: {cnt_values}'

    expected_test_events = sum(1 for value in cnt_values if value % 2 == 0)
    assert len(test_values) == expected_test_events, (
        f'expected {expected_test_events} test writes, got {len(test_values)}'
    )
    assert test_values, 'no test values captured from log'
    assert test_values[0] == 0, 'first test write should see reset value'
    assert all(prev < curr for prev, curr in zip(test_values, test_values[1:])), (
        'test values should be strictly increasing'
    )

def test_array_write():
    run_test('array_write', build_top, check)

if __name__ == '__main__':
    test_array_write()
