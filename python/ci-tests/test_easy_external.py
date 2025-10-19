from assassyn.frontend import *
from assassyn.backend import elaborate
from assassyn import utils


class Driver(Module):

    def __init__(self):
        super().__init__(ports={})

    @module.combinational
    def build(self, lhs: Module, rhs: Module):
        cnt = RegArray(UInt(32), 1)
        v = cnt[0]
        cnt[0] = cnt[0] + UInt(32)(1)
        lhs.async_called(data=v)
        rhs.async_called(data=v)


class ForwardData(Module):
    def __init__(self):
        super().__init__(
            ports={'data': Port(UInt(32))},
        )

    @module.combinational
    def build(self):
        data = self.pop_all_ports(True)
        return data

@external
class ExternalAdder(ExternalSV):
    '''External SystemVerilog adder module.'''

    a: WireIn[UInt(32)]
    b: WireIn[UInt(32)]
    c: WireOut[UInt(32)]

    __source__: str = "python/ci-tests/resources/adder.sv"
    __module_name__: str = "adder"


class Adder(Downstream):

    def __init__(self):
        super().__init__()

    @downstream.combinational
    def build(self, a: Value, b: Value):
        #here we assumed user explicitly know the direction of the external module ports
        a = a.optional(UInt(32)(1))
        b = b.optional(UInt(32)(1))

        c = ExternalAdder(a=a, b=b)
        log("downstream: {} + {} = {}", a, b, c.c)


def test_easy_external():
    sys = SysBuilder('easy_external')
    with sys:
        driver = Driver()
        lhs = ForwardData()
        rhs = ForwardData()
        a = lhs.build()
        b = rhs.build()

        adder = Adder()

        driver.build(lhs, rhs)
        adder.build(a, b)

    config = {
        'verilog': utils.has_verilator(),
        'simulator': True,
        'sim_threshold': 100,
        'idle_threshold': 100
    }
    simulator_path, verilator_path = elaborate(sys, **config)

    raw = utils.run_simulator(simulator_path)

    if verilator_path:
        #raw = utils.run_verilator(verilator_path)
        assert "downstream:" in raw, "Expected log output not found"


if __name__ == '__main__':
    test_easy_external()
