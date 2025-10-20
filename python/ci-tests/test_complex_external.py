from assassyn.frontend import *
from assassyn.backend import elaborate
from assassyn import utils
import assassyn
@external
class ExternalAdder(ExternalSV):
    '''External SystemVerilog adder module.'''

    a: WireIn[UInt(32)]
    b: WireIn[UInt(32)]
    c: WireOut[UInt(32)]

    __source__: str = "python/ci-tests/resources/adder.sv"
    __module_name__: str = "adder"


@external
class ExternalRegister(ExternalSV):
    '''External SystemVerilog Reg.'''

    reg_in: WireIn[Bits(32)]
    reg_out: RegOut[Bits(32)]

    __source__: str = "python/ci-tests/resources/reg.sv"
    __module_name__: str = "register"
    __has_clock__: bool = True
    __has_reset__: bool = True


class Sink(Module):

    def __init__(self):
        super().__init__(
            ports={
                'a': Port(UInt(32)),
                'b': Port(UInt(32))
            },
        )

    @module.combinational
    def build(self, reg: Array):
        a, b = self.pop_all_ports(True)
        log("Sink received:  {} + {} = {}", a, b, reg[0])

class Adder(Module):

    def __init__(self):
        super().__init__(
            ports={
                'a': Port(UInt(32)),
                'b': Port(UInt(32)),
            },
        )

    @module.combinational
    def build(self , sink: Sink):
        a, b = self.pop_all_ports(True)

        ext_adder = ExternalAdder(a=a, b=b)

        log("Adder: {} + {} = {}", a, b, ext_adder.c)

        ex_reg = ExternalRegister(reg_in=ext_adder.c.bitcast(Bits(32)))
        
        sink.async_called(a=a, b=b)

        return ex_reg.reg_out



class Driver(Module):

    def __init__(self):
        super().__init__(ports={})

    @module.combinational
    def build(self, adder: Adder):
        # The code below is equivalent
        # cnt = RegArray(Int(32), 0)
        # v = cnt[0]
        # (cnt & self)[0] <= v + Int(32)(1)
        # NOTE: cnt[0]'s new value is NOT visible until next cycle.
        # cond = v < Int(32)(100)
        # with Condition(cond):
        #     adder.async_called(a = v, b = v)
        cnt = RegArray(UInt(32), 1)
        (cnt & self)[0] <= cnt[0] + UInt(32)(1)
        cond = cnt[0] < UInt(32)(100)
        with Condition(cond):
            adder.async_called(a = cnt[0], b = cnt[0])

def check_raw(raw):
    cnt = 0
    for i in raw.split('\n'):
        if 'Adder:' in i:
            line_toks = i.split()
            c = line_toks[-1]
            a = line_toks[-3]
            b = line_toks[-5]
            assert int(a) + int(b) == int(c)
            cnt += 1
    assert cnt == 100, f'cnt: {cnt} != 100'


def test_complex_external():
    # NOTE: The name of the system should be unique within all the testcases,
    # because we currently have no locks to exclusively own a folder, under the
    # context of multi-thread testing.
    sys = SysBuilder('complex_external')
    with sys:
        sink = Sink()
        

        adder = Adder()
        reg = adder.build(sink)
        sink.build(reg)

        driver = Driver()
        driver.build(adder)

    print(sys)

    config = assassyn.backend.config(
            verilog=utils.has_verilator(),
            sim_threshold=200,
            idle_threshold=200,
            random=True)

    simulator_path, verilator_path = elaborate(sys, **config)

    raw = utils.run_simulator(simulator_path)
    check_raw(raw)

    if verilator_path:
        raw = utils.run_verilator(verilator_path)
        check_raw(raw)


if __name__ == '__main__':
    test_complex_external()
