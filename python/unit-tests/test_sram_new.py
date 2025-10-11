import assassyn
from assassyn.frontend import *
from assassyn import backend
from assassyn import utils
from assassyn.ir.module.downstream import Downstream, combinational


@external
class ExternalSRAM(ExternalSV):
    '''External SystemVerilog SRAM module.'''

    address: WireIn[Bits(9)]
    wd: WireIn[Bits(32)]
    banksel: WireIn[Bits(1)]
    read: WireIn[Bits(1)]
    write: WireIn[Bits(1)]

    dataout: WireOut[Bits(32)]

    __source__: str = "python/unit-tests/resources/sram.sv"
    __module_name__: str = "sram"
    __has_clock__: bool = True
    __has_reset__: bool = True


class MemUser(Module):

    def __init__(self, width):
        super().__init__(
            ports={'rdata': Port(Bits(width))},
        )

    @module.combinational
    def build(self):
        width = self.rdata.dtype.bits
        rdata = self.pop_all_ports(False)
        rdata = rdata.bitcast(Int(width))
        k = Int(width)(128)
        delta = rdata + k
        log('{} + {} = {}', rdata, k, delta)
        log('Read {}', rdata)


class Driver(Module):

    def __init__(self):
        super().__init__(ports={})

    @module.combinational
    def build(self, width, external_sram:ExternalSRAM, user):
        cnt = RegArray(Int(width), 1)
        v = cnt[0]
        we = v[0:0]
        re = ~we
        plused = v + Int(width)(1)
        waddr = plused[0:8]
        raddr = v[0:8]
        addr = we.select(waddr, raddr).bitcast(Bits(9))
        (cnt & self)[0] <= plused
        en = cnt[0] < Int(32)(512)  # Always select bank 0
        #sram = SRAM(width, 512, init_file)
        #sram.build(we, re, addr, v.bitcast(Bits(width)), user)
        #TODO support the constant value directly
        external_sram.in_assign(
            address=addr,
            wd=v.bitcast(Bits(width)),
            banksel=en,
            read=re,
            write=we,
        )
        data_out = external_sram.dataout
        log('banksel {} data_out {}', en, data_out)
        with Condition(we):
            log('[driver] Wrote {} to address {}', v, waddr)
        with Condition(re):
            user.async_called(rdata=data_out)
            log('Read address {} , dataout {}', addr, data_out)



def check(raw):
    for line in raw.splitlines():
        if '[memuser' in line:
            toks = line.split()
            c = int(toks[-1])
            b = int(toks[-3])
            a = int(toks[-5])
            assert c % 2 == 1 or a == 0, f'Expected odd number or zero, got {line}'
            assert c == a + b, f'{a} + {b} = {c}'

def impl(sys_name, width, init_file, resource_base):
    sys = SysBuilder(sys_name)
    with sys:
        user = MemUser(width)
        user.build()
        external_sram = ExternalSRAM()
        # Build the driver
        driver = Driver()
        driver.build(width, external_sram, user)

    config = backend.config(sim_threshold=200, idle_threshold=200, resource_base=resource_base, verilog=utils.has_verilator())

    simulator_path, verilator_path = backend.elaborate(sys, **config)

    raw = utils.run_simulator(simulator_path)

    if verilator_path:
        raw = utils.run_verilator(verilator_path)
    #check(raw)

def test_memory():
    impl('memory', 32, None, None)

if __name__ == "__main__":
    test_memory()
    #test_memory_init()
    #test_memory_wide()
