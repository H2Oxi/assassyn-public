use crate::{
    builder::{
      system::ModuleKind,
      SysBuilder,
    },
    ir::{

      expr::{self, subcode}, instructions::BlockIntrinsic, module, node::{BaseNode,  ModuleRef}, visitor::Visitor, Opcode
    },
  };
  use crate::ir::node::ExprRef;
  use crate::ir::node::IsElement;
  use std::{collections::HashMap, hash::Hash};

  use crate::ir::Expr;
  use crate::builder::PortInfo;
  use crate::ir::DataType;
  use crate::ir::node::NodeKind;
  use crate::ir::Module;
  use crate::ir::expr::subcode::Binary;
  use crate::ir::Block;
  use crate::ir::node::Parented;
  use crate::xform::arbiter::find_module_with_callers;
  use crate::ir::IntImm;

  #[derive(Debug, Clone)]
  pub struct SubModule {
    buffered_barriers: Vec<usize>, 
    buffered_ports: Vec<usize>,
    buffered_expr: HashMap<usize, BaseNode>,  // HashMap<childs_key, EXPR>
    

  }

  #[derive(Debug, Clone)]
  pub struct SubModuleContainer{
    module_name: String,
    sub_module_map: HashMap<usize, SubModule>, // HashMap<key, SubModule>
    sub_module_order: HashMap<usize, usize>, // HashMap<order, SubModule_key>
    caller_expr: HashMap<usize, BaseNode>,  // HashMap<childs_key, EXPR>
    barrier_expr: HashMap<usize, BaseNode>,  // HashMap<childs_key, EXPR>
  }

  
  pub struct GatherModulesToCut<'sys> {
    sys: &'sys SysBuilder,
    to_rewrite: Option<ModuleRef<'sys>>,
    has_barrier: bool,
    submodule_container_map: HashMap<usize, SubModuleContainer>, // HashMap<key, SubModuleContainer>

}

  impl<'sys> GatherModulesToCut<'sys> {
    pub fn new(sys: &'sys SysBuilder) -> Self {
        Self {
            sys,
            to_rewrite: None,
            has_barrier: false,
            submodule_container_map: HashMap::new(),
        }
    }

    pub fn print_submodules(&mut self) {
        for (key, value) in self.submodule_container_map.iter() {
            println!("Submodule: {:?}, {:?}", key, value);
        }
    }

    pub fn submodule_container_map(&self) -> &HashMap<usize, SubModuleContainer> {
        &self.submodule_container_map
    }

}

impl<'sys> Visitor<()> for GatherModulesToCut<'sys> {
    fn visit_module(&mut self, module: ModuleRef<'_>) -> Option<()> 

    {
        match module.get_name() {
            "testbench" => {
            }
            _ => {
                if let Some(x) = self.visit_block(module.get_body()) {
                    return Some(x);
                }
            }
        }
        None
    }

    fn visit_expr<'a>(&mut self, expr: ExprRef<'a>) -> Option<()> {
        println!("Expr: {:?}", expr.get_key());
        match expr.get_opcode() {
            Opcode::BlockIntrinsic { intrinsic } => {
                if intrinsic == subcode::BlockIntrinsic::Barrier {
                    self.has_barrier = true;
                    println!("Buffered expr: {:?}", expr.get_operand_value(0).as_ref().unwrap().get_key());
                    
                }
            }
            Opcode::FIFOPush => {
                let parent_module = expr.get_parent().as_ref::<Block>(self.sys).unwrap().get_module();
                println!("----------Push expr_op0: {:?} , expr_op1: {:?} , Parent_module: {:?}"
                    , expr.get_operand_value(0).as_ref().unwrap().get_key()
                    , expr.get_operand_value(1).as_ref().unwrap().get_key()
                    , parent_module.as_ref::<Module>(self.sys).unwrap().get_name());
            }
            _ => {}
        }
        None
    }

    fn enter<'a>(&mut self, _sys: &SysBuilder) -> Option<()> {
        
        for module in self.sys.module_iter(ModuleKind::Module) {
            self.has_barrier = false;
            self.to_rewrite = Some(module.clone());
            self.visit_module(module.clone());
            if self.has_barrier {
                let mut visitor = GraphVisitor::new(self.sys);
                self.submodule_container_map.insert(module.get_key()
                    , SubModuleContainer{module_name: module.get_name().to_string()
                        , sub_module_map: HashMap::new()
                        , sub_module_order: HashMap::new()
                        , caller_expr: HashMap::new()
                        , barrier_expr: HashMap::new()});
                if let Some(module) = self.to_rewrite.take() {
                    visitor.visit_module(module);
                }
                visitor
                    .graph
                    .gather_barrier_level(self.sys);

                visitor
                    .graph
                    .gather_submodules(self.sys);
                self.submodule_container_map.get_mut(&module.get_key()).unwrap().sub_module_map = visitor.graph.submodule_map.clone();
                self.submodule_container_map.get_mut(&module.get_key()).unwrap().sub_module_order = visitor.graph.submodule_order.clone();
                self.submodule_container_map.get_mut(&module.get_key()).unwrap().caller_expr = visitor.graph.caller_expr.clone();
                self.submodule_container_map.get_mut(&module.get_key()).unwrap().barrier_expr = visitor.graph.barrier_expr.clone();
            }

        }
        Some(())
    }
}


#[derive(Debug, Clone)]
pub struct NodeData {
  mom: usize,
  child: usize,
}

pub struct DependencyGraph {
  adjacency: Vec<NodeData>,  //  HashMap<mom, childs>
  expr_hashmap: HashMap<usize, BaseNode>,  // HashMap<key, EXPR>
  caller_expr: HashMap<usize, BaseNode>,  // HashMap<childs_key, EXPR>
  barrier_expr: HashMap<usize, BaseNode>,  // HashMap<childs_key, EXPR>

  barrier_list: Vec<usize>,
  current_module_name: String,
  submodule_barrier_map: HashMap<usize, Vec<usize>>, // HashMap<submodule_key, Vec<barrier_key>>
  module_ports: Vec<usize>,
  module_outputs: Vec<usize>,
  submodule_map: HashMap<usize, SubModule>, // HashMap<key, SubModule>
  submodule_order: HashMap<usize, usize>, // HashMap<order, SubModule_key>
 
}

impl Default for DependencyGraph {
  fn default() -> Self {
    Self::new()
  }
}

impl DependencyGraph {
  pub fn new() -> Self {
    Self {
      adjacency: Vec::new(),
      barrier_list: Vec::new(),
      expr_hashmap: HashMap::new(),
      caller_expr: HashMap::new(),
      barrier_expr: HashMap::new(),


      current_module_name: String::new(),
      submodule_barrier_map: HashMap::new(),
      module_ports: Vec::new(),
      module_outputs: Vec::new(),
      submodule_map: HashMap::new(),
      submodule_order: HashMap::new(),
    }
  }

  pub fn set_current_module_name(&mut self, name: String) {
      self.current_module_name = name;
  }

  pub fn add_edge(&mut self, mom: usize, child: usize) {
      self.adjacency.push(NodeData{mom:mom, child:child});
      //self.expr_hashmap.entry(child);
  }


  pub fn gather_submodules(&mut self, sys: &SysBuilder) {
    

    #[allow(clippy::too_many_arguments)]
    fn dfs(
      graph: &Vec<NodeData>,
      current:  usize ,
      path: &mut Vec<usize>,
      all_paths: &mut Vec<Vec<usize>>,
      current_level: &mut i32 ,
      submodule_barrier_map: &HashMap<usize, Vec<usize>>,
      submodule_map: &mut HashMap<usize, SubModule>,
      submodule_order: &mut HashMap<usize, usize>,
      expr_hashmap: &HashMap<usize, BaseNode>,
      module_outputs: &Vec<usize>,
      used_nodes: &mut Vec<usize>,

    ) {
      path.push(current);
      println!("current_level: {:?}", *current_level);
      println!("current_node: {:?}", current);

      let new_level;
      let current_clone = current.clone();

      let key_containing_v = submodule_barrier_map.iter()
        .find_map(|(&key, vec)| if vec.contains(&current_clone) { Some(key) } else { None });

      match key_containing_v {
          Some(submodule_key) => {
              //make sure the barrier node is not ordered
          
              //target to the next level
              
              if  submodule_map.get_mut(submodule_order.get(&(*current_level as usize)).unwrap()).unwrap().buffered_barriers.is_empty() {
                  new_level = *current_level + 1;
                  submodule_order.insert(new_level as usize, submodule_key);
                  //save barrier nodes for current submodule
                  submodule_map
                      .get_mut(submodule_order.get(&(*current_level as usize)).unwrap())
                      .unwrap()
                      .buffered_barriers = submodule_barrier_map.get(&submodule_key).unwrap().clone();
                  //save ports for new submodule
                  submodule_map.insert(submodule_key, SubModule{buffered_barriers: Vec::new(), 
                      buffered_ports: submodule_barrier_map.get(&submodule_key).unwrap().clone(), buffered_expr: HashMap::new()});
              }
              else{
                  if submodule_map.get(submodule_order.get(&(*current_level as usize)).unwrap()).unwrap().buffered_ports.contains(&current_clone) {
                      if * current_level == 0 {
                          new_level = *current_level;
                      }
                      else {
                          new_level = *current_level - 1;
                      }
                  }
                  else { 
                      if submodule_map.get(submodule_order.get(&(*current_level as usize)).unwrap()).unwrap().buffered_barriers.contains(&current_clone)
                      {
                          new_level = *current_level + 1;
                      }
                      else {
                          new_level = *current_level;
                      }     
                  }
              }
              
              
          
          },
          None => {
            new_level = *current_level;
          },
      }
      println!("current: {:?}  , current_clone: {:?}", current, current_clone);
      println!("submodule_map_key: {:?}", submodule_order.get(&(*current_level as usize)).unwrap());
      println!("submodule_map_get_mut: {:?}", submodule_map.get_mut(submodule_order.get(&(*current_level as usize)).unwrap()).unwrap());
      println!("------current: {:?}", current);
      println!("expr_hashmap: {:?}", expr_hashmap.get(&current).unwrap());
                  
                  
      let mut has_child = false;
      if !used_nodes.contains(&current) {
            submodule_map
              .get_mut(submodule_order.get(&(*current_level as usize)).unwrap())
              .unwrap()
              .buffered_expr
              .insert(current, expr_hashmap.get(&current).unwrap().clone());
            for edge in graph
            {

                if edge.mom == current {
                    has_child = true;
                  //find the next node.and we got the current expression,then we should justify the current expression is a buffered node or not
                  //if it is in one of the submodule_barrier_map, then we should update or create a new submodule in next order
                  //if it is not in the submodule_barrier_map, then we can just continue to find the next node
                  //just update the current submodule
                  
                  
              
                  println!("goto_node: {:?}", edge.child);
                  *current_level = new_level;
              
              
                  dfs(
                    graph,
                    edge.child,
                    path,
                    all_paths,
                    current_level,
                    submodule_barrier_map,
                    submodule_map,
                    submodule_order,
                    expr_hashmap,
                    module_outputs,
                    used_nodes,
                  );
                }

            }
            if !has_child {
                all_paths.push(path.clone());
                
            }
        }else {
            all_paths.push(path.clone());
        }
      
      if module_outputs.contains(&current) {
        if  submodule_map.get_mut(submodule_order.get(&(*current_level as usize)).unwrap()).unwrap().buffered_barriers.is_empty() {
            submodule_map
                .get_mut(submodule_order.get(&(*current_level as usize)).unwrap())
                .unwrap()
                .buffered_barriers = module_outputs.clone();
        }
      }
      used_nodes.push(current as usize);
      println!("used_nodes: {:?}", used_nodes);

      path.pop();

    }

    let mut path: Vec<usize> = Vec::new();
    let mut used_nodes: Vec<usize> = Vec::new();
    
    println!("=== Gathering Submodule Information ===");

    for module_port in self.module_ports.iter() 
    {   
        let mut all_paths = vec![];   
        let mut level = 0;

        //create the first submodule_map
        if self.submodule_map.is_empty() {
            println!("Create the first submodule_map");
            self.submodule_map.insert(*module_port, SubModule{buffered_barriers: Vec::new(), buffered_ports: self.module_ports.clone(), buffered_expr: HashMap::new()});
            self.submodule_order.insert(0, *module_port);
            println!("submodule_order: {:?}", self.submodule_order);
        }
        //need to change dfs to support 

        dfs(
          &self.adjacency,
          *module_port,
          &mut path,
          &mut all_paths,
          &mut level,
          &self.submodule_barrier_map,
          &mut self.submodule_map,
          &mut self.submodule_order,
          &self.expr_hashmap,
          &self.module_outputs,
          &mut used_nodes,
        );
    

   
        for path in all_paths 
        {
            let path_with_edges: Vec<String> = path
                .windows(2)
                .zip(path.iter())
                .map(|(nodes, edge)| format!("\"{}\" -> {}", edge, nodes[1]))
                .collect();

            println!("Path:  {}", path_with_edges.join("    "));
        }
    }


    for (key, value) in self.submodule_map.iter() {
        println!("Submodule: {:?}, {:?}", key, value);
    }
  

  }


  pub fn gather_barrier_level(&mut self, sys: &SysBuilder) {
    

    #[allow(clippy::too_many_arguments)]
    fn dfs(
      graph: &Vec<NodeData>,
      current: usize,
      path: &mut Vec<usize>,
      all_paths: &mut Vec<Vec<usize>>,

    ) {
      path.push(current);
      
      let mut has_neighbors = false;
      for edge in graph
      {
          
          if edge.mom == current {
            has_neighbors = true;
            dfs(
              graph,
              edge.child,
              path,
              all_paths,
            );
          }
          
      }
      if !has_neighbors && path.len() > 1  {
          all_paths.push(path.clone());
      }

      path.pop();

    }

    println!("=== gathering barrier levels ===");  

    for buffer_node in self.barrier_list.iter()
    {
        let mut all_paths = vec![];
        let mut path: Vec<usize> = Vec::new();

        let all_do_not_contain = self.submodule_barrier_map.values().all(|vec| !vec.contains(&*buffer_node));

        //if we don't find the barrier node in the existing submodule_barrier_map, we should create a new submodule and then insert the barrier node into it
        if all_do_not_contain {
            self.submodule_barrier_map.insert(*buffer_node, vec![*buffer_node]);

            dfs(
                &self.adjacency,
                *buffer_node,
                &mut path,
                &mut all_paths,
              );
 
            for path in all_paths {
                let mut output_string = String::new();

                for (i, key) in path.iter().enumerate() {
                    if i > 0 {
                        output_string.push_str(" -> ");
                    if let Some(base_node) = self.expr_hashmap.get(key) {
                        output_string.push_str(&format!("{}: {:?}", key, base_node));
                        if let Ok(expr) = base_node.as_ref::<Expr>(sys) {
                            println!("Processing expression operands for Expr: {}", expr.get_name());

                            let not_save_nodes = expr.get_opcode() == Opcode::FIFOPush || expr.get_opcode() == Opcode::Bind || expr.get_opcode() == Opcode::AsyncCall;
                            if !not_save_nodes
                            {
                                if expr.get_opcode() == Opcode::Slice 
                                {
                                    let oprend_key = expr.get_operand_value(0).unwrap().get_key();
                                    println!("Operand key: {}", oprend_key); 
                                    // if not in the expr_hashmap.key, then it should be a buffered node    
                                    if !path.contains(&oprend_key) {
                                        println!("Buffered node: {:?}", oprend_key);
                                        // if not in the buffered_nodes, then it should be a buffered node
                                        if !self.submodule_barrier_map.get(buffer_node).unwrap().contains(&oprend_key) {
                                            self.submodule_barrier_map.get_mut(buffer_node).unwrap().push(oprend_key);
                                            
                                        }
                                    
                                    }  
                                }
                                else{
                                    for operand in expr.operand_iter() {
                                        println!("Operand key: {}", operand.get_value().get_key()); 
                                        // if not in the expr_hashmap.key, then it should be a buffered node    
                                        if !path.contains(&operand.get_value().get_key()) {
                                            println!("Buffered node: {:?}", operand.get_value().get_key());
                                            // if not in the buffered_nodes, then it should be a buffered node
                                            if !self.submodule_barrier_map.get(buffer_node).unwrap().contains(&operand.get_value().get_key()) {
                                                self.submodule_barrier_map.get_mut(buffer_node).unwrap().push(operand.get_value().get_key());

                                            }
                                        
                                        }   
                                    }
                                }
                            }
                            else {

                            }
                        
                            if let Some(first_operand) = expr.get_operand(0) {
                                println!("First operand key: {}", first_operand.get_key());
                            }
                        } else {
                            println!("The provided BaseNode is not an expression.");
                        }
                    }
                }
            }
        
            println!("Path: {}", output_string);

            let path_with_edges: Vec<String> = path
                  .windows(2)
                  .zip(path.iter())
                  .map(|(nodes, edge)| format!("\"{}\" -> {}", edge, nodes[1]))
                  .collect();

              println!("Path:  {}", path_with_edges.join("    "));
            }

            }

    }

    

    for (key, value) in self.submodule_barrier_map.iter() {
        println!("Submodule: {:?}, {:?}", key, value);
    }
    
  
  }

}

pub struct GraphVisitor<'sys> {
    pub graph: DependencyGraph,
  
    pub sys: &'sys SysBuilder,
}

impl<'sys> GraphVisitor<'sys> { 
    pub fn new(sys: &'sys SysBuilder)  -> Self {
      Self {
          graph: DependencyGraph::new(),
          sys,
      }
        
  }
}

impl<'sys> Visitor<()> for GraphVisitor<'sys>  {
  fn visit_expr(&mut self, expr: ExprRef<'_>) -> Option<()> {

    let mut is_barrier = false;
    let mut is_output = false;

    match expr.get_opcode() {
        Opcode::BlockIntrinsic { intrinsic } => {
            if intrinsic == subcode::BlockIntrinsic::Barrier {
                println!("Buffered expr: {:?}", expr.get_operand_value(0).as_ref().unwrap().get_key());
                self.graph.barrier_list.push(expr.get_operand_value(0).as_ref().unwrap().get_key());
                is_barrier = true;
            }
        }
        Opcode::Bind => {
            is_output = true;
        }
        Opcode::AsyncCall => {
            is_output = true;
        }

        _ => {is_barrier = false; is_output = false;}
    }
    if expr.get_opcode() == Opcode::FIFOPush || is_output {
        self.graph.caller_expr.insert(expr.get_key(), expr.elem);
    }

    if is_barrier{
        self.graph.barrier_expr.insert(expr.get_key(), expr.elem);
    }

    if !(is_barrier || is_output)
    {
        self.graph.expr_hashmap.insert(expr.get_key(), expr.elem);
        println!("expr_hashmap_insert: {:?}", expr.get_key());
        println!("opcode: {:?}", expr.get_opcode());
    
        if (expr.get_opcode() == Opcode::Load) || (expr.get_opcode() == Opcode::FIFOPop) {
              self
                .graph
                .module_ports
                .push(expr.get_key());
        }
    
        if expr.get_opcode() == Opcode::FIFOPush 
        || expr.get_opcode() == Opcode::Store {
            //if let Some(DataType::UInt(_)) = operand_ref.get_value().get_dtype(self.sys) {} else
        {
            self.graph.module_outputs.push(expr.get_operand_value(1).as_ref().unwrap().get_key());
            println!("-----module_outputs: {:?}", expr.get_operand_value(1).as_ref().unwrap().get_key());
            
        }
    
        }else {
            if expr.get_opcode() == Opcode::Slice 
            {
                self.graph
                        .add_edge(expr.get_operand_value(0).unwrap().get_key(), expr.get_key());
            }
            else{
                for operand_ref in expr.operand_iter() {
                    self.graph
                        .add_edge(operand_ref.get_value().get_key(), expr.get_key());

                    print!("mom: {:?},child: {:?}", operand_ref.get_value().get_key(), expr.get_key());
                }
            }
        }
        
    }
    None
  }
}


pub struct CutModules<'a> {
    sys: &'a mut SysBuilder,
    submodule_container_map: HashMap<usize, SubModuleContainer>, // HashMap<key, SubModuleContainer>
}

impl<'a> CutModules<'a> {
    pub fn new(sys: &'a mut SysBuilder ) -> Self {
        Self {
            sys,
            submodule_container_map: HashMap::new(),
        }
    }

    pub fn set_submodule_container_map(&mut self, submodule_container_map: HashMap<usize, SubModuleContainer>) {
        self.submodule_container_map = submodule_container_map;
    }

    pub fn is_submodule_valid(&self, key: usize) -> bool {
        self.submodule_container_map.contains_key(&key)
    }

    pub fn print_submodules(&mut self) {
        for (key, value) in self.submodule_container_map.iter() {
            println!("Submodule: {:?}, {:?}", key, value);
        }
    }

    //#TODO add the check for the expr is valid for remapping
    pub fn is_expr_valid(&self, node_map: &HashMap<usize, BaseNode> , expr: &ExprRef    ) -> bool {
         true
    }

    pub fn cut_modules(&mut self) {
        for (container_key, container) in self.submodule_container_map.iter() {
            // create the new modules inside each module
            let original_module_name = &container.module_name;

            let module_with_multi_caller = find_module_with_callers(self.sys);
            println!("module_with_multi_caller: {:?}", module_with_multi_caller);
            let mut new_submodule_caller = HashMap::new();
            let mut nodes_waiting_for_asyncall = HashMap::new();

            // we must make sure the order of the submodule is correct
            let mut sub_module_order_rev: Vec<_> = container.sub_module_order.iter().collect();
            sub_module_order_rev.sort_by_key(|(k, _)| *k);

            //remove the barrier expr
            for (key, basenode) in container.barrier_expr.iter() {
                //#TODO need to do remove here
                basenode.as_mut::<Expr>(self.sys).unwrap().erase_from_parent();
                
            }
            
            for (order, submod_key) in sub_module_order_rev {
                let submodule = container
                .sub_module_map
                .get(submod_key)
                .expect("Submodule key not found in sub_module_map");

            let mut port_type_map:HashMap<usize,DataType> = HashMap::new();


            if *order > 0 {
                //middle module
                for port_id in submodule.buffered_ports.iter() {
                    let right_basenode:BaseNode = *nodes_waiting_for_asyncall.get(port_id).unwrap();
                    let right = right_basenode.as_ref::<Expr>(self.sys).unwrap();
                    println!("right type: {:?}",right.elem.get_dtype(self.sys).unwrap());
                    port_type_map.insert(*port_id, right.elem.get_dtype(self.sys).unwrap());

                }

                let new_module_name = format!("{}_{}", original_module_name, order);
                let new_module_ports: Vec<PortInfo> = submodule
                    .buffered_ports
                    .iter()
                    .map(|port_id| {
                        //#TODO support change the data type
                        PortInfo::new(&format!("buffered_{}", port_id),  port_type_map.get(port_id).unwrap().clone())
                    })
                    .collect();
                
                let new_module = self.sys.create_module(&new_module_name, new_module_ports);

                new_submodule_caller.insert(order,new_module);

                println!("new_module_name: {:?}", new_module_name);
                //FIFO valid Gen
                let mut last_fifo_valid_handle: Option<BaseNode> = None;
                let mut ports_remapping_map = HashMap::new();
                

                self.sys.set_current_module(new_module);
                
                for port_id in submodule.buffered_ports.iter() {
                    let actual_port_name = format!("buffered_{}", port_id);
                
                    let port_handle = {
                        let module_handle = new_module.as_ref::<Module>(&self.sys).unwrap();
                        module_handle.get_fifo(&actual_port_name).unwrap().upcast()
                    };
                    ports_remapping_map.insert(*port_id, port_handle);
                    let fifo_valid_handle = self.sys.create_fifo_valid(port_handle);
                
                    // If we already have a `last_fifo_valid_handle`, combine:
                    if let Some(prev_handle) = last_fifo_valid_handle {
                        let fifo_valid_comb_handle = self.sys.create_bitwise_and(prev_handle, fifo_valid_handle);
                        last_fifo_valid_handle = Some(fifo_valid_comb_handle);
                        println!("Combining old handle = {:?} with new handle = {:?}", 
                                 prev_handle, fifo_valid_handle);
                    }else {
                        last_fifo_valid_handle = Some(fifo_valid_handle);

                    }

                }
                if let Some(fifo_all_valid) = last_fifo_valid_handle {
                    self.sys.create_wait_until(fifo_all_valid);
                }

                //bind init
                if *order > 1
                {
                    self.sys.set_current_module(*new_submodule_caller.get(&(order - 1)).unwrap());
                    println!("new_submodule_caller: {:?}", new_submodule_caller.get(&(order - 1)).unwrap());
                    let bind_init = self.sys.get_init_bind(new_module);
                    
                    for port_id in submodule.buffered_ports.iter() {

                        self.sys.bind_arg(
                            bind_init,
                            format!("buffered_{}", port_id),
                            *nodes_waiting_for_asyncall.get(port_id).unwrap()
                        );
                    }
                    self.sys.create_async_call(bind_init); 
                    nodes_waiting_for_asyncall.clear();
                }else {
                    let original_module = self.sys.get_module(original_module_name).unwrap();
                    self.sys.set_current_module(original_module.elem );
                    
                    let bind_init = self.sys.get_init_bind(new_module);
                    
                    for port_id in submodule.buffered_ports.iter() {

                        self.sys.bind_arg(
                            bind_init,
                            format!("buffered_{}", port_id),
                            *nodes_waiting_for_asyncall.get(port_id).unwrap()
                        );
                    }
                    self.sys.create_async_call(bind_init); 
                    nodes_waiting_for_asyncall.clear();
                }
             

                self.sys.set_current_module(new_module);
                //FIFO pop Gen
                let mut node_remapping_map = HashMap::new();
                for (k,port) in ports_remapping_map.iter() {
                    let pop_node = self.sys.create_fifo_pop( *port);
                    node_remapping_map.insert(*k, pop_node);
                    println!("----FIFO pop: {:?}, key: {:?}", pop_node, k);
                }
                println!("node_remapping_map: {:?}", node_remapping_map);
                let mut bind_init: Option<BaseNode> = None;
                let mut bind_node_str_map = HashMap::new();
                let mut bind_nodes_map:Vec<BaseNode> = Vec::new();
                let mut nodes_to_remove:Vec<BaseNode> = Vec::new();

                if  *order == (container.sub_module_order.len() - 1) 
                {
                    //for barrier_node in submodule.buffered_barriers.iter()  

                    
                    for expr_key in container.caller_expr.keys() 
                    {
                        //get the bind push expr
                        if let Some(expr) = container.caller_expr.get(expr_key) {
                            println!("callee_expr: {:?}", expr);
                            let expr_bind = expr.as_ref::<Expr>(self.sys).unwrap();
                            if expr_bind.get_opcode() == Opcode::FIFOPush {
                                println!("expr_bind: {:?}, and  {:?}"
                                    , expr_bind.get_operand_value(0).as_ref().unwrap().to_string(self.sys) 
                                    , expr_bind.get_operand_value(1).as_ref().unwrap().to_string(self.sys));
                                let port_str = expr_bind.get_operand_value(0).as_ref().unwrap().to_string(self.sys);
                                let node_id = expr_bind.get_operand_value(1).as_ref().unwrap().get_key();
                                bind_node_str_map.insert(node_id, port_str);
                                

                            }
                            else if expr_bind.get_opcode() == Opcode::Bind 
                            {
                                //#TODO here we assumed only one module was caller as long as having barrier
                                for (callee,caller) in module_with_multi_caller.iter() {
                                    if  caller.contains(&expr) {
                                        println!("callee: {:?}, caller: {:?}", callee, caller);
                                        let bind_init_temp = self.sys.get_init_bind(*callee);
                                        bind_init = Some(bind_init_temp);
                                        
                                    }
                                }
                                bind_nodes_map.push(expr.clone());
                            }
                            else if expr_bind.get_opcode() == Opcode::AsyncCall {
                                bind_nodes_map.push(expr.clone());
                            }
                        }
                    }

                }

                //at first,we just support the expr needed at buffer test
                // and also, the expr also need to be ordered
                let mut ordered_expr_map: Vec<_> =submodule.buffered_expr.iter().collect();
                ordered_expr_map.sort_by_key(|(key, _value)| *key);
                for (child_key, base_node) in ordered_expr_map {
                    if base_node.get_kind() == NodeKind::Expr {
                        let expr = base_node.as_ref::<Expr>(self.sys).unwrap();

                        println!("new_expr_opcode: {:?}  | dst: {:?} ", expr.get_opcode(), child_key);
                        for operand_ref in expr.operand_iter() {
                            let operand = operand_ref.get_value();
                            println!("new_expr_operand: {:?}", operand.get_key());
                            if let Some(new_node) = node_remapping_map.get(&operand.get_key()) {
                                println!("new_node: {:?}", new_node);
                            }
                        }
                        
                        let opcode = expr.get_opcode();
                        let mut new_expr_handle: Option<BaseNode> = None;
                        // copy expr to new module
                        match opcode {
                            Opcode::Binary { binop } => match binop {
                                Binary::Add => {

                                    let new_expr = self.sys.create_add(
                                        *node_remapping_map.get(&expr.get_operand_value(0).as_ref().unwrap().get_key()).unwrap(), 
                                        *node_remapping_map.get(&expr.get_operand_value(1).as_ref().unwrap().get_key()).unwrap());
                                    new_expr_handle = Some(new_expr);

                                },
                                Binary::Sub => {
                                    let new_expr = self.sys.create_sub(
                                        *node_remapping_map.get(&expr.get_operand_value(0).as_ref().unwrap().get_key()).unwrap(), 
                                        *node_remapping_map.get(&expr.get_operand_value(1).as_ref().unwrap().get_key()).unwrap());
                                    new_expr_handle = Some(new_expr);
                                },
                                Binary::Mul => {
                                    let new_expr = self.sys.create_mul(
                                        *node_remapping_map.get(&expr.get_operand_value(0).as_ref().unwrap().get_key()).unwrap(), 
                                        *node_remapping_map.get(&expr.get_operand_value(1).as_ref().unwrap().get_key()).unwrap());
                                    new_expr_handle = Some(new_expr);
                                },
                                Binary::BitwiseAnd | Binary::BitwiseOr | Binary::BitwiseXor => {},
                                Binary::Shl | Binary::Shr => {},
                                Binary::Mod => {},
                              },
                              Opcode::Cast {  cast} => match cast {
                                expr::subcode::Cast::BitCast => {
                                    //#TODO need to support the data type
                                    let new_expr = self.sys.create_bitcast(
                                        *node_remapping_map.get(&expr.get_operand_value(0).as_ref().unwrap().get_key()).unwrap(),
                                         DataType::int_ty(32));
                                    new_expr_handle = Some(new_expr);
                                },
                                _ => {}
                              },
                              Opcode::Slice => {
                                let start = expr.get_operand_value(1);
                                if start.unwrap().get_kind() == NodeKind::IntImm {
                                    println!("start: {:?}", start.unwrap().as_ref::<IntImm>(self.sys).unwrap().get_value());
                                }
                                

                                let end = expr.get_operand_value(2);
                                if end.unwrap().get_kind() == NodeKind::IntImm {
                                    println!("end: {:?}", end.unwrap().as_ref::<IntImm>(self.sys).unwrap().get_value());
                                }
                                if let Some(start_node) =start {
                                    if let Some(end_node) = end {
                                        let new_expr = self.sys.create_slice(
                                            *node_remapping_map.get(&expr.get_operand_value(0).as_ref().unwrap().get_key()).unwrap(),
                                            start_node,
                                            end_node);
                                        new_expr_handle = Some(new_expr);
                                    }
                                }
                               

                              
                              },              
                          _ => {
                                println!("-------{:?}   is not supported",opcode);
                            }
                        }
                        if let Some(new_expr) = new_expr_handle {
                            node_remapping_map.insert(*child_key, new_expr);
                            println!("insert_new_expr: {:?} with origin key: {:?}", new_expr, *child_key);
                            //remove the original expr
                            //#TODO need to be more careful here
                            println!("base_node parents: {:?}", base_node.get_parent(self.sys));
                            println!("nodes_to_remove.push : {:?}", base_node);
                            
                            nodes_to_remove.push(*base_node);
                        }

                        println!("create finished");
                    }
                    
                }
                //FIFO push Gen
                for port_id in submodule.buffered_barriers.iter() {
                    println!("port_id: {:?}", port_id);
                    nodes_waiting_for_asyncall.insert(port_id,*node_remapping_map.get(port_id).unwrap());
                }
                println!("nodes_waiting_for_asyncall: {:?}", nodes_waiting_for_asyncall);
                if  *order == (container.sub_module_order.len() - 1) 
                {


                    let mut bind_expr_map: Vec<_> =container.caller_expr.iter().collect();
                    bind_expr_map.sort_by_key(|(key, _value)| *key);
                    bind_expr_map.reverse();
                    for (k,node) in bind_expr_map {
                        println!("erase_from_parent: {:?}", node);
                        node.as_mut::<Expr>(self.sys).unwrap().erase_from_parent();
                        
                    }
                    nodes_to_remove.reverse();
                    for node in nodes_to_remove {
                        println!("erase_from_parent: {:?}", node);
                        node.as_mut::<Expr>(self.sys).unwrap().erase_from_parent();
                    }
                    

                    if let Some(bind) = bind_init
                    {
                        for (node_id,port_str) in bind_node_str_map
                        {
                            let test= self.sys.bind_arg(
                                bind,
                                port_str,
                                *node_remapping_map.get(&node_id).unwrap());
                            println!("bind_arg: {:?}", test);
                        }
                        let test= self.sys.create_async_call(bind);
                        println!("create_async_call: {:?}", test);
                    }
                    
                }
            }
            else
            {
                //FIFO push Gen
                for port_id in submodule.buffered_barriers.iter() {
                    println!("port_id: {:?}", port_id);
                    nodes_waiting_for_asyncall.insert(port_id,submodule.buffered_expr.get(port_id).unwrap().clone());
                }
            }
            
            
    
            }
            
            
            
            println!("{}", self.sys);

            //try to remove expr

            //get to the original module
            //self.sys.slab.remove(self.sys.get_module(original_module_name).unwrap().get_key());
            


            //assign the push at the final module

            
            
        }
    }



}



