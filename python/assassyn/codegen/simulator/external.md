# what we should do for truly support the ExternalSV in simulator

here, the `pipemul_external_simulator` and `pipemul_external_verilator` under `note/manually_sim_pipemul` is the golden reference for the code generation work flow in our project, when talking to the ExternalSV usage. 

- Step 1: 
At first, you can find that I have just commented out the `Elaborating external module ExternalMultiplier_31b44` part of the code in `module.rs `. Which means that we should not generate such part of code follow the traditional module's style. So, please at first fix this. Make sure external module will not generate such full class as normal module in `module.rs`.


- Step 2: 
Secondly. as you can see under the direction in `note/manually_sim_pipemul/pipemul_external_verilator/verilated_mul_pipe_simple`, we need to do much more support when we have reset and clock in external module. in `workspace/pipemul_external_verilator`, which is the code we generated from current project. you can just compare it with the golden one and just add the support in our generation flow.


- Step 3:
Now, we need to support the wire assign implementation. 

This is the generated code for wire-assign now: 
```
  // External wire assign: ExternalAdder_2aad2.a = tmp_a_1;
  {
    let stamp = sim.stamp;
    let data = ValueCastTo::<u32>::cast(&tmp_a_1);
    sim
      .ExternalAdder_2aad2_a
      .push
      .push(FIFOPush::new(stamp + 50, data, "Adder_766c3"));
  };
  {
    let stamp = sim.stamp - sim.stamp % 100 + 100;
    sim.ExternalAdder_2aad2_event.push_back(stamp)
  };
```
It is absolutely wrong for our implementation.

And the code below is what I want you generate.
```
  // External wire assign: ExternalAdder_2aad2.a = tmp_a_1;
  sim.ExternalAdder_2aad2_ffi.set_a(ValueCastTo::<u32>::cast(&tmp_a_1));
```


