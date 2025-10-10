import assassyn
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
class ExternalMultiplier(ExternalSV):
    '''External SystemVerilog multiplier module.'''

    a: Input[UInt(32)]
    b: Input[UInt(32)]
    in_valid: Input[Bits(1)]

    p: Output[UInt(64)]
    out_valid: Output[Bits(1)]

    __source__: str = "python/ci-tests/resources/mul_pipe_simple.sv"
    __module_name__: str = "mul_pipe_simple"
    __has_clock__: bool = True
    __has_reset__: bool = True

class Sink(Module):

    def __init__(self):
        super().__init__(
            ports={'data': Port(UInt(32*2))},
        )

    @module.combinational
    def build(self):
        data = self.pop_all_ports(True)
        log("Sink: {}", data)

class Wrapper(Downstream):

    def __init__(self):
        super().__init__()

    @downstream.combinational
    def build(self, a: Value, b: Value, ext_mul: ExternalMultiplier , sink: Sink):
        #here we assumed user explicitly know the direction of the external module ports
        a = a.optional(UInt(32)(1))
        b = b.optional(UInt(32)(1))
        in_valid = a > UInt(32)(2)  # Always valid input

        ext_mul.in_assign(a=a, b=b, in_valid=in_valid)
        out_ready = ext_mul.out_valid
        p = ext_mul.p

        with Condition(out_ready):
            sink.async_called(data=p)

        log("downstream: {} * {} ", a, b)
        log("in_valid: {}, out_ready: {}, p: {}", in_valid, out_ready, p)


def test_pipemul_external():
    sys = SysBuilder('pipemul_external')
    with sys:
        driver = Driver()
        lhs = ForwardData()
        rhs = ForwardData()
        a = lhs.build()
        b = rhs.build()

        ext_mul = ExternalMultiplier()
        wrapper = Wrapper()
        sink = Sink()
        sink.build()

        driver.build(lhs, rhs)
        wrapper.build(a, b, ext_mul, sink)

    print(sys)

    config = assassyn.backend.config(
            verilog = utils.has_verilator(),  # Force verilog generation
            sim_threshold=100,
            idle_threshold=100)

    simulator_path, verilator_path = elaborate(sys, **config )

    if verilator_path:
        raw = utils.run_verilator(verilator_path)


if __name__ == '__main__':
    test_pipemul_external()
