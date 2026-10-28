//! 外壳日志：汇聚后端 stdout/stderr、外壳事件与前端日志到同一个文件。
//!
//! 输出：`{STORAGE_ROOT}/logs/desktop/desktop.log`，10MB × 3 轮转。
//! 每行带启动相对时间戳，便于对齐「外壳启动 → 后端启动 → 前端初始化」的时间线。

use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::Instant;

const MAX_BYTES: u64 = 10 * 1024 * 1024;
const MAX_FILES: usize = 3;

struct Logger {
    file: File,
    path: PathBuf,
    size: u64,
}

static LOGGER: Mutex<Option<Logger>> = Mutex::new(None);
static STARTED_AT: OnceLock<Instant> = OnceLock::new();

fn started_at() -> Instant {
    *STARTED_AT.get_or_init(Instant::now)
}

/// 初始化日志文件；重复调用会重新打开同一个文件。
pub fn init(log_dir: &Path) -> Result<(), String> {
    fs::create_dir_all(log_dir).map_err(|e| format!("创建日志目录失败 {:?}: {}", log_dir, e))?;
    let _ = started_at();

    let path = log_dir.join("desktop.log");
    let file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("打开日志文件失败 {:?}: {}", path, e))?;
    let size = file.metadata().map(|meta| meta.len()).unwrap_or(0);

    let mut guard = LOGGER
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    *guard = Some(Logger { file, path, size });
    Ok(())
}

/// 写入一行日志。tag 为 `shell` / `backend:out` / `backend:err` / `frontend`。
pub fn log(tag: &str, message: impl std::fmt::Display) {
    let elapsed_ms = started_at().elapsed().as_millis();
    let line = format!("+{}ms [{}] {}\n", elapsed_ms, tag, message);
    // 同时输出到终端，`tauri dev` 时可直接观察
    print!("{}", line);

    let mut guard = LOGGER
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some(logger) = guard.as_mut() {
        logger.write(&line);
    }
}

impl Logger {
    fn write(&mut self, line: &str) {
        if self.size >= MAX_BYTES {
            self.rotate();
        }
        if self.file.write_all(line.as_bytes()).is_err() {
            return;
        }
        self.size += line.len() as u64;
    }

    fn rotate(&mut self) {
        let _ = self.file.flush();
        let base = self.path.to_string_lossy().to_string();

        // 丢弃最旧的一份：desktop.log.3
        let oldest = format!("{}.{}", base, MAX_FILES);
        let _ = fs::remove_file(&oldest);

        // desktop.log.2 -> .3, desktop.log.1 -> .2
        for index in (1..MAX_FILES).rev() {
            let from = format!("{}.{}", base, index);
            let to = format!("{}.{}", base, index + 1);
            if Path::new(&from).exists() {
                let _ = fs::rename(&from, &to);
            }
        }

        // desktop.log -> desktop.log.1
        let _ = fs::rename(&base, format!("{}.1", base));

        if let Ok(file) = OpenOptions::new().create(true).append(true).open(&self.path) {
            self.file = file;
            self.size = 0;
        }
    }
}
