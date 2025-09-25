from pathlib import Path

from assassyn import utils
import assassyn
from assassyn.frontend import *
from assassyn.backend import elaborate
from assassyn.ir.module.external import ExternalSV
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

class ExternalAdder(ExternalSV):
    '''External SystemVerilog adder module.'''
    
    def __init__(self, **in_wire_connections):
        super().__init__(
            file_path="python/unit-tests/resources/adder.sv",
            module_name="adder",
            in_wires={
                'a': UInt(32),
                'b': UInt(32),
            },
            out_wires={
                'c': UInt(32),
            },
            **in_wire_connections
        )
    
    def __getattr__(self, name):
        # Allow accessing output wires as attributes
        if hasattr(self, 'out_wires') and name in self.out_wires:
            return self.out_wires[name]
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")


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
