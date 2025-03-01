use crate::{
    builder::{
      system::{ ModuleKind},
      SysBuilder,
    },
    ir::{

      node::{BaseNode,  ModuleRef},
      visitor::Visitor,
      expr::subcode, 
      Opcode
    },
  };
  use crate::ir::node::ExprRef;
  use crate::ir::node::IsElement;
  use std::collections::HashMap;
  use crate::ir::Expr;
  use crate::builder::PortInfo;
  use crate::ir::DataType;

  pub struct GatherModulesToCut<'sys> {
    sys: &'sys SysBuilder,
    to_rewrite: Option<ModuleRef<'sys>>,
}

  impl<'sys> GatherModulesToCut<'sys> {
    pub fn new(sys: &'sys SysBuilder) -> Self {
        Self {
            sys,
            to_rewrite: None,
        }
    }
}

impl<'sys> Visitor<()> for GatherModulesToCut<'sys> {
    fn visit_module(&mut self, module: ModuleRef<'_>) -> Option<()> 

    {
        match module.get_name() {
            "driver" | "testbench" => {
                // 这两个模块不在设计内部，直接跳过
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
                    let mut visitor = GraphVisitor::new();
                    if let Some(module) = self.to_rewrite.take() {
                        visitor.visit_module(module);
                    }
                    println!("Buffered expr: {:?}", expr.get_operand_value(0).as_ref().unwrap().get_key());
                    visitor
                        .graph
                        .rewrite(self.sys,expr.get_operand_value(0).as_ref().unwrap().get_key());
                }
            }
            _ => {}
        }
        None
    }

    fn enter<'a>(&mut self, _sys: &SysBuilder) -> Option<()> {
        // 遍历所有模块，将它们依次作为当前要处理的模块
        for module in self.sys.module_iter(ModuleKind::Module) {
            self.to_rewrite = Some(module.clone());
            self.visit_module(module.clone());
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
    buffered_nodes: Vec<usize>, 

    buffered_expr: HashMap<usize, BaseNode>,  // HashMap<childs_key, EXPR>
    moved_expr: HashMap<usize, BaseNode>,  // HashMap<childs_key, EXPR>

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
        expr_hashmap: HashMap::new(),
        buffered_expr: HashMap::new(),
        moved_expr: HashMap::new(),
        buffered_nodes: Vec::new(),
      }
    }
  
    pub fn add_edge(&mut self, mom: usize, child: usize) {
        self.adjacency.push(NodeData{mom:mom, child:child});
        //self.expr_hashmap.entry(child);
    }

    pub fn rewrite_modules(&mut self, sys: &mut SysBuilder) {
        let mut all_ports = vec![];
        for (key, expr) in self.moved_expr.iter() 
        {
            println!("Moved expr: {:?}, {:?},{:?} ", key, expr,expr.as_ref::<Expr>(sys).unwrap().get_opcode());
    
        }
        for (key, expr) in self.buffered_expr.iter() 
        {
            println!("Buffered expr: {:?}, {:?},  {:?}", key, expr,expr.as_ref::<Expr>(sys).unwrap().get_opcode());
    
        }
        for key in self.buffered_nodes.iter() {
            let name = format!("buffered_{}", key);
            //#TODO support change the data type
            all_ports.push(PortInfo::new(&name, DataType::int_ty(32)));
            println!("Buffered node: {:?}", key);

        }
        //#TODO create a new module and insert the moved exprs into it
        //#TODO change the logic to support multiple cutting points

        let barrier_m_0 = sys.create_module(
            "barrier_module",  //#TODO change the name to a right one
            all_ports,
        );

        
    }
  
    pub fn rewrite(&mut self, sys: &mut SysBuilder, buffer_node: usize) {
      let mut all_paths = vec![];
      self.buffered_nodes.push(buffer_node);

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
  
        let mut path: Vec<usize> = Vec::new();

        dfs(
          &self.adjacency,
          buffer_node,
          &mut path,
          &mut all_paths,

        );
      
    println!("=== Cutting pipeline ===");

    for key in self.expr_hashmap.keys() {
        println!("Key: {}", key);
    }


    // we should create a new module and insert the moved exprs into it

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
                        self.buffered_expr.insert(*key, expr.elem.clone());
                        for operand in expr.operand_iter() {
                            println!("Operand key: {}", operand.get_value().get_key());


                            // if not in the expr_hashmap.key, then it should be a buffered node

                            if !path.contains(&operand.get_value().get_key()) {
                                println!("Buffered node: {:?}", operand.get_value().get_key());
                                // if not in the buffered_nodes, then it should be a buffered node
                                if !self.buffered_nodes.contains(&operand.get_value().get_key()) {
                                    self.buffered_nodes.push(operand.get_value().get_key());
                                }
                            
                            }

                        }
                    }
                    else {
                        self.moved_expr.insert(*key, expr.elem.clone());
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
    
    self.rewrite_modules(sys);


    }
  }
  
  pub struct GraphVisitor {
    pub graph: DependencyGraph,
}

impl GraphVisitor {
    pub fn new() -> Self {
        Self {
            graph: DependencyGraph::new(),
        }
    }
}

impl Visitor<()> for GraphVisitor {
    fn visit_expr(&mut self, expr: ExprRef<'_>) -> Option<()> {

        //if expr.get_opcode() != Opcode::Log
        //{
            for operand_ref in expr.operand_iter() {
                self.graph
                    .add_edge(operand_ref.get_value().get_key(), expr.get_key());
                self.graph.expr_hashmap.insert(expr.get_key(), expr.elem);
                print!("mom: {:?},child: {:?}", operand_ref.get_value().get_key(), expr.get_key());

            }
            
        //}
        None
    }
}
  
