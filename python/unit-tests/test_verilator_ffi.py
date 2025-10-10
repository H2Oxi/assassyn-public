from pathlib import Path

from assassyn import utils
import assassyn
from assassyn.frontend import *
from assassyn.backend import elaborate
from assassyn.codegen.simulator.external import external_handle_field


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

    a: Input[UInt(32)]
    b: Input[UInt(32)]
    c: Output[UInt(32)]

    __source__: str = "python/unit-tests/resources/adder.sv"
    __module_name__: str = "adder"


class Adder(Downstream):

    def __init__(self):
        super().__init__()

    @downstream.combinational
    def build(self, a: Value, b: Value):
        #here we assumed user explicitly know the direction of the external module ports
        a = a.optional(UInt(32)(1))
        b = b.optional(UInt(32)(1))

        c = a + b

        log("downstream: {} + {} = {}", a, b, c)
def _build_system():
    sys = SysBuilder('verilator_ffi')
    with sys:
        driver = Driver()
        lhs = ForwardData()
        rhs = ForwardData()
        a = lhs.build()
        b = rhs.build()
        
        ext_adder = ExternalAdder()
        adder = Adder()

        driver.build(lhs, rhs)
        adder.build(a, b)

    return sys


def test_verilator_ffi_generation():
    sys = _build_system()

    config = assassyn.backend.config(
        simulator=True,
        verilog=False,
        sim_threshold=32,
        idle_threshold=32,
    )

    simulator_manifest, verilator_path = elaborate(sys, **config)
    #assert verilator_path is None
#
    #manifest_path = Path(simulator_manifest)
    #utils.run_external_interfaces(manifest_path, release=False, offline=True)



if __name__ == '__main__':
    test_verilator_ffi_generation()
