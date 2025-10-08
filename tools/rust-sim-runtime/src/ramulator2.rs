use std::error::Error;
use std::ffi::{c_char, c_void, CString};

// Platform-specific libloading imports
#[cfg(target_os = "macos")]
use libloading::os::unix::{Library, Symbol, RTLD_GLOBAL, RTLD_LAZY};

#[cfg(target_os = "linux")]
use libloading::{Library, Symbol};

#[cfg(not(any(target_os = "macos", target_os = "linux")))]
use libloading::{Library, Symbol};

// Platform-specific library loading macro
#[cfg(target_os = "macos")]
macro_rules! load_library {
    ($path:expr) => {
        {
            let path_with_ext = format!("{}.dylib", $path);
            unsafe { Library::open(Some(&path_with_ext), RTLD_GLOBAL | RTLD_LAZY)? }
        }
    };
}

#[cfg(target_os = "linux")]
macro_rules! load_library {
    ($path:expr) => {
        {
            let path_with_ext = format!("{}.so", $path);
            unsafe { Library::new(&path_with_ext)? }
        }
    };
}

#[cfg(not(any(target_os = "macos", target_os = "linux")))]
macro_rules! load_library {
    ($path:expr) => {
        {
            let path_with_ext = format!("{}.dll", $path);
            unsafe { Library::new(&path_with_ext)? }
        }
    };
}

#[repr(C)]
pub struct Request {
  pub addr: i64,
  pub addr_vec: Vec<i32>,
  pub type_id: i32,
  pub source_id: i32,
  pub command: i32,
  pub final_command: i32,
  pub is_stat_updated: bool,
  pub arrive: i64,
  pub depart: i64,
  pub scratchpad: [i32; 4],
  pub callback: Option<extern "C" fn(*mut Request)>,
  pub m_payload: *mut c_void,
}

#[repr(C)]
pub struct Response {
  pub valid: bool,
  pub addr: usize,
  pub data: *mut c_void, // Using pointer instead of BigUInt for C compatibility
}
type CRamualator2Wrapper = *mut c_void;
pub type RequestCallback = extern "C" fn(*mut Request, *mut c_void);
type ResponseCallback = extern "C" fn(*mut Response, *mut c_void);

pub struct MemoryInterface {
  lib: Library,
  wrapper: CRamualator2Wrapper,
}

impl MemoryInterface {
  pub unsafe fn new(lib: Library) -> Result<Self, Box<dyn Error>> {
    let dram_new: Symbol<unsafe extern "C" fn() -> CRamualator2Wrapper> = lib.get(b"dram_new")?;
    let wrapper = dram_new();

    Ok(Self { lib, wrapper })
  }

  pub unsafe fn init(&self, config_path: &str) {
    let c_path = CString::new(config_path).unwrap();
    let dram_init: Symbol<unsafe extern "C" fn(CRamualator2Wrapper, *const c_char)> =
      self.lib.get(b"dram_init").unwrap();
    dram_init(self.wrapper, c_path.as_ptr());
  }

  pub unsafe fn frontend_tick(&self) {
    let frontend_tick: Symbol<unsafe extern "C" fn(CRamualator2Wrapper)> =
      self.lib.get(b"frontend_tick").unwrap();
    frontend_tick(self.wrapper);
  }

  pub unsafe fn memory_system_tick(&self) {
    let memory_system_tick: Symbol<unsafe extern "C" fn(CRamualator2Wrapper)> =
      self.lib.get(b"memory_system_tick").unwrap();
    memory_system_tick(self.wrapper);
  }

  #[allow(non_snake_case)]
  pub unsafe fn get_memory_tCK(&self) -> f32 {
    let get_memory_tck: Symbol<unsafe extern "C" fn(CRamualator2Wrapper) -> f32> =
      self.lib.get(b"get_memory_tCK").unwrap();
    get_memory_tck(self.wrapper)
  }

  pub unsafe fn send_request(
    &self,
    addr: i64,
    is_write: bool,
    callback: RequestCallback,
    ctx: *mut c_void,
  ) -> bool {
    let send_request: Symbol<
      unsafe extern "C" fn(CRamualator2Wrapper, i64, bool, RequestCallback, *mut c_void) -> bool,
    > = self.lib.get(b"send_request").unwrap();
    send_request(self.wrapper, addr, is_write, callback, ctx)
  }

  pub unsafe fn finish(&self) {
    let my_finish: Symbol<unsafe extern "C" fn(CRamualator2Wrapper)> =
      self.lib.get(b"finish").unwrap();
    my_finish(self.wrapper);
  }
}

impl Drop for MemoryInterface {
  fn drop(&mut self) {
    unsafe {
      let dram_delete: Symbol<unsafe extern "C" fn(CRamualator2Wrapper)> =
        self.lib.get(b"dram_delete").unwrap();
      dram_delete(self.wrapper);
    }
  }
}

// Platform-independent constructor for MemoryInterface
impl MemoryInterface {
  /// Create a new MemoryInterface with platform-specific library loading
  pub fn new_from_path(lib_path: &str) -> Result<Self, Box<dyn Error>> {
    let lib = load_library!(lib_path);
    unsafe { Self::new(lib) }
  }
}
