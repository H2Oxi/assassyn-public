from assassyn.frontend import *
from assassyn.backend import elaborate
from assassyn import utils
import assassyn

class Adder2(Module):

    def __init__(self):
        super().__init__(
            ports={
                'a': Port(Int(32)),
                'b': Port(Int(32)),
            },
        )

    @module.combinational
    def build(self):
        a, b = self.pop_all_ports(True)
        c = a * b
        d = a + b + c
        log("combi: {} + {} + {}*{} = {} ", a, b, a, b, d)

# Condition 2: The barrier is inside the conditional block
class Adder1_bridge(Module):

    def __init__(self):
        super().__init__(
            ports={
                'd_barrier': Port(Int(64)),
                'c_buffer': Port(Int(32)),

            },
        )

    @module.combinational
    def build(self,adder: Adder2):
        d_barrier , c_buffer= self.pop_all_ports(True)
        f = d_barrier * c_buffer         
        adder.async_called(a = f.bitcast(Int(32)), b = d_barrier.bitcast(Int(32)))
        

class Adder1(Module):

    def __init__(self):
        super().__init__(
            ports={
                'a': Port(Int(32)),
                'b': Port(Int(32)),
                'c': Port(Int(32)),
            },
        )

    @module.combinational
    def build(self,barrier_module: Adder1_bridge):
        a, b , c= self.pop_all_ports(True)
        e = a * b
        with Condition(e < Int(64)(100)):
            d = a + b + e
            barrier_module.async_called(d_barrier = d.bitcast(Int(64)), 
                           c_buffer = c.bitcast(Int(32)))





# condition 1: barrier is outside the condition block
'''class Adder1_bridge(Module):

    def __init__(self):
        super().__init__(
            ports={
                'e_barrier': Port(Int(32)),
                'a_p_b_buffer': Port(Int(32)),
                'c_buffer': Port(Int(32)),
            },
        )

    @module.combinational
    def build(self,adder: Adder2):
        e_barrier, a_p_b_buffer , c_buffer= self.pop_all_ports(True)

        d = a_p_b_buffer + e_barrier
        with Condition(d < Int(32)(100)):
            f = d * c_buffer         
            adder.async_called(a = f.bitcast(Int(32)), b = d.bitcast(Int(32)))
        

class Adder1(Module):

    def __init__(self):
        super().__init__(
            ports={
                'a': Port(Int(32)),
                'b': Port(Int(32)),
                'c': Port(Int(32)),
            },
        )

    @module.combinational
    def build(self,barrier_module: Adder1_bridge):
        a, b , c= self.pop_all_ports(True)
        e = a * b
        a_p_b_buffer = a + b      
        c_buffer = c

        barrier_module.async_called(e_barrier = e.bitcast(Int(32)), 
                           a_p_b_buffer = a_p_b_buffer.bitcast(Int(32)),
                           c_buffer = c_buffer.bitcast(Int(32)))'''


    


class Driver(Module):

    def __init__(self):
        super().__init__(ports={})

    @module.combinational
    def build(self, adder: Adder1):
        # The code below is equivalent
        # cnt = RegArray(Int(32), 0)
        # v = cnt[0]
        # cnt[0] = v + Int(32)(1)
        # NOTE: cnt[0]'s new value is NOT visible until next cycle.
        # cond = v < Int(32)(100)
        # with Condition(cond):
        #     adder.async_called(a = v, b = v)
        cnt = RegArray(Int(32), 1)
        cnt[0] = cnt[0] + Int(32)(1)
        cnt_div2_temp = cnt[0] + Int(32)(1)
        cnt_div2 = Int(32)(0)
        cnt_div2 = cnt[0][0:0].select(cnt[0], cnt_div2_temp)

        cond = cnt[0] < Int(32)(100)

        with Condition(cond):
            adder.async_called(a = cnt_div2, b = cnt_div2 , c = cnt_div2)




def test_async_call():
    sys = SysBuilder('Comb_Block_barrier_gold')
    with sys:
        adder2 = Adder2()
        adder2.build()

        adder1_bridge = Adder1_bridge()
        adder1_bridge.build(adder2)

        adder1 = Adder1()
        adder1.build(adder1_bridge)

        driver = Driver()
        driver.build(adder1)

    print(sys)

    config = assassyn.backend.config(
            verilog=utils.has_verilator(),
            sim_threshold=200,
            idle_threshold=200,
            random=True)

    #simulator_path, verilator_path = elaborate(sys, **config)
    simulator_path, verilator_path = elaborate(sys, **config)

    raw = utils.run_simulator(simulator_path)



if __name__ == '__main__':
    test_async_call()
