//! Python 后端子进程管理：启动、stdout 行协议、状态机、退出与停机。
//!
//! 状态机只由三类输入驱动：
//! 1. 后端 stdout 行（[`handle_stdout_line`]）
//! 2. 子进程退出（[`handle_exit`]）
//! 3. 启动超时定时器（generation 计数防误触发）

use crate::utils::{self, StdioEvent, StdioStream};
use crate::{config, logger, sys};
use std::path::PathBuf;
use std::process::ChildStdin;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager};

pub const START_TIMEOUT_MS: u64 = 15_000;
const MARKER_PREFIX: &str = "[TOGO] ";

#[derive(Debug, Clone)]
pub enum BackendError {
    Message(String),
}

impl BackendError {
    fn describe(&self) -> String {
        match self {
            BackendError::Message(message) => format!("Message({})", message),
        }
    }
}

#[derive(Debug, Clone)]
pub enum BackendState {
    NotStarted,
    Starting,
    Migrating,
    Ready,
    Failed(BackendError),
    Exited(i32),
}

impl BackendState {
    fn is_starting(&self) -> bool {
        matches!(self, BackendState::Starting)
    }

    fn is_migrating(&self) -> bool {
        matches!(self, BackendState::Migrating)
    }

    fn is_waiting_for_ready(&self) -> bool {
        matches!(self, BackendState::Starting | BackendState::Migrating)
    }

    fn is_running(&self) -> bool {
        matches!(
            self,
            BackendState::Starting | BackendState::Migrating | BackendState::Ready
        )
    }

    pub fn describe(&self) -> String {
        match self {
            BackendState::NotStarted => "NotStarted".to_string(),
            BackendState::Starting => "Starting".to_string(),
            BackendState::Migrating => "Migrating".to_string(),
            BackendState::Ready => "Ready".to_string(),
            BackendState::Failed(error) => format!("Failed({})", error.describe()),
            BackendState::Exited(code) => format!("Exited({})", code),
        }
    }
}

#[derive(Clone)]
pub struct BackendLaunchConfig {
    pub storage_root: PathBuf,
    pub port: u16,
    pub token: String,
}

static STATE: Mutex<BackendState> = Mutex::new(BackendState::NotStarted);
static TIMEOUT_GENERATION: AtomicU64 = AtomicU64::new(0);
static BACKEND_PID: Mutex<Option<u32>> = Mutex::new(None);
static BACKEND_STDIN: Mutex<Option<ChildStdin>> = Mutex::new(None);

fn with_state<T>(f: impl FnOnce(&mut BackendState) -> T) -> T {
    let mut state = STATE
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    f(&mut state)
}

pub fn snapshot() -> BackendState {
    STATE
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .clone()
}

pub fn is_ready() -> bool {
    matches!(snapshot(), BackendState::Ready)
}

pub fn state_label() -> &'static str {
    match snapshot() {
        BackendState::NotStarted => "not_started",
        BackendState::Starting => "starting",
        BackendState::Migrating => "migrating",
        BackendState::Ready => "ready",
        BackendState::Failed(_) => "failed",
        BackendState::Exited(_) => "exited",
    }
}

pub fn error_message() -> Option<String> {
    match snapshot() {
        BackendState::Failed(BackendError::Message(message)) => Some(message),
        BackendState::Exited(0) => Some("后端已停止".to_string()),
        BackendState::Exited(code) => Some(format!("后端异常退出（代码 {}）", code)),
        _ => None,
    }
}

/// 重置为未启动（重启流程使用）。
pub fn reset_to_not_started() {
    with_state(|state| *state = BackendState::NotStarted);
}

/// 尝试 NotStarted → Starting；返回是否由本次调用完成迁移。
pub fn transition_to_starting() -> bool {
    with_state(|state| {
        if matches!(state, BackendState::NotStarted) {
            *state = BackendState::Starting;
            true
        } else {
            false
        }
    })
}

pub fn fail(app: &tauri::AppHandle, message: String) {
    cancel_start_timeout();
    with_state(|state| *state = BackendState::Failed(BackendError::Message(message.clone())));
    logger::log("shell", format!("backend failed: {}", message));
    let _ = app.emit("backend-error", message);
    crate::tray::refresh(app);
}

/// 拉起后端进程并接管 stdout/stderr/stdin。
pub fn start(app: &tauri::AppHandle) -> Result<(), String> {
    let launch = app.state::<BackendLaunchConfig>().inner().clone();
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|e| format!("获取资源目录失败: {}", e))?;
    let exe_dir = std::env::current_exe()
        .map_err(|e| format!("获取可执行文件路径失败: {}", e))?
        .parent()
        .ok_or_else(|| "可执行文件没有父目录".to_string())?
        .to_path_buf();
    // debug 构建下 exe 位于 tauri/build/target[/<triple>]/debug，向上逐级找仓库根
    let repo_root = find_repo_root(&exe_dir);

    let mut command = sys::platform::get_command(&resource_dir, &repo_root)?;
    command
        .env("TOGOSPACE_DESKTOP", "1")
        .env("STORAGE_ROOT", &launch.storage_root)
        .env("TOGOSPACE_DESKTOP_PORT", launch.port.to_string())
        .env("TOGOSPACE_DESKTOP_TOKEN", &launch.token)
        .env("TOGOSPACE_PARENT_PID", std::process::id().to_string())
        // dev 模式下 Python 直接写管道，确保 marker 即时可见
        .env("PYTHONUNBUFFERED", "1");

    logger::log(
        "shell",
        format!(
            "starting backend: resource_dir={:?}, repo_root={:?}, port={}",
            resource_dir, repo_root, launch.port
        ),
    );

    let mut platform_state = sys::platform::setup_command(&mut command);
    let mut child = command
        .spawn()
        .map_err(|e| format!("启动后端进程失败: {}", e))?;
    sys::platform::post_spawn(&mut platform_state, &mut child);

    let pid = child.id();
    *BACKEND_STDIN
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) =
        sys::platform::take_stdin(&mut platform_state);
    *BACKEND_PID
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(pid);
    logger::log("shell", format!("backend spawned, pid={}", pid));

    schedule_start_timeout(app.clone());

    let app_for_stdout = app.clone();
    let app_for_exit = app.clone();
    utils::watch_piped_stdio(child, move |event| match event {
        StdioEvent::ReaderStarted(stream) => {
            logger::log("shell", format!("{}_READER_STARTED", stream.label()))
        }
        StdioEvent::ReaderFinished(stream) => {
            logger::log("shell", format!("{}_READER_FINISHED", stream.label()))
        }
        StdioEvent::Line(StdioStream::Stdout, line) => {
            handle_stdout_line(&app_for_stdout, &line)
        }
        StdioEvent::Line(StdioStream::Stderr, line) => logger::log("backend:err", line),
        StdioEvent::ReadError(stream, message) => logger::log(
            "backend:err",
            format!("{} READ ERROR: {}", stream.label(), message),
        ),
        // 运行期后端自行退出（含崩溃）走这里
        StdioEvent::Exited(code) => handle_exit(&app_for_exit, code),
    });

    Ok(())
}

/// 解析并处理一行后端 stdout；非 marker 行只做日志转发。
pub fn handle_stdout_line(app: &tauri::AppHandle, line: &str) {
    logger::log("backend:out", line);

    let Some(payload) = line.trim_end().strip_prefix(MARKER_PREFIX) else {
        return;
    };

    if let Some(url) = payload.strip_prefix("READY url=") {
        handle_ready(app, url.trim());
    } else if payload == "MIGRATION_START" {
        handle_migration_start(app);
    } else if payload == "MIGRATION_END" {
        handle_migration_end(app);
    } else if let Some(rest) = payload.strip_prefix("ERROR ") {
        let (code, message) = parse_error_marker(rest);
        let display = match code {
            Some(code) => format!("{}（错误码 {}）", message, code),
            None => message,
        };
        with_state(|state| *state = BackendState::Failed(BackendError::Message(display.clone())));
        cancel_start_timeout();
        logger::log("shell", format!("backend reported error: {}", display));
        let _ = app.emit("backend-error", display);
        crate::tray::refresh(app);
    } else {
        logger::log(
            "shell",
            format!("unknown backend marker ignored: {}", payload),
        );
    }
}

/// `ERROR code=<n> message=<text>` → (code, message)
fn parse_error_marker(rest: &str) -> (Option<i32>, String) {
    if let Some(after_code) = rest.strip_prefix("code=") {
        if let Some((code_text, message)) = after_code.split_once(" message=") {
            if let Ok(code) = code_text.trim().parse::<i32>() {
                return (Some(code), message.trim().to_string());
            }
        }
    }
    // 无 code 或 code 非法时整体作为消息，避免丢失原文
    (None, rest.trim().to_string())
}

fn handle_ready(app: &tauri::AppHandle, url: &str) {
    let changed = with_state(|state| {
        if state.is_waiting_for_ready() {
            *state = BackendState::Ready;
            true
        } else {
            false
        }
    });

    if !changed {
        logger::log(
            "shell",
            format!(
                "backend ready ignored, state={}",
                snapshot().describe()
            ),
        );
        return;
    }

    cancel_start_timeout();
    logger::log("shell", format!("backend ready, url={}", url));

    if let Some(backend_url) = app.try_state::<crate::BackendUrl>() {
        if let Ok(mut value) = backend_url.0.lock() {
            *value = url.to_string();
        }
    }

    let launch = app.state::<BackendLaunchConfig>().inner().clone();
    if let Ok(mut config) = config::load_or_create(&launch.storage_root) {
        config::record_ready(&launch.storage_root, &mut config);
    }

    let _ = app.emit("backend-ready", ());
    crate::on_backend_ready(app);
}

fn handle_migration_start(app: &tauri::AppHandle) {
    let changed = with_state(|state| {
        if state.is_starting() {
            *state = BackendState::Migrating;
            true
        } else {
            false
        }
    });

    if changed {
        // 迁移期间取消启动超时，迁移结束再重新计时
        cancel_start_timeout();
        logger::log("shell", "backend migration started");
        let _ = app.emit("backend-migration-start", ());
        crate::tray::refresh(app);
    } else {
        logger::log(
            "shell",
            format!(
                "backend migration start ignored, state={}",
                snapshot().describe()
            ),
        );
    }
}

fn handle_migration_end(app: &tauri::AppHandle) {
    let changed = with_state(|state| {
        if state.is_migrating() {
            *state = BackendState::Starting;
            true
        } else {
            false
        }
    });

    if changed {
        logger::log("shell", "backend migration ended");
        let _ = app.emit("backend-migration-end", ());
        schedule_start_timeout(app.clone());
    } else {
        logger::log(
            "shell",
            format!(
                "backend migration end ignored, state={}",
                snapshot().describe()
            ),
        );
    }
}

fn handle_exit(app: &tauri::AppHandle, code: i32) {
    cancel_start_timeout();
    *BACKEND_PID
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = None;
    drop(
        BACKEND_STDIN
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .take(),
    );

    let quitting = crate::is_quitting();
    let should_emit_error = with_state(|state| match state {
        BackendState::Ready => {
            *state = BackendState::Exited(code);
            code != 0
        }
        BackendState::Failed(_) => false,
        _ => {
            *state = BackendState::Exited(code);
            true
        }
    });

    logger::log("shell", format!("backend exited, code={}", code));

    if should_emit_error && !quitting {
        let message = if code == 0 {
            "后端已退出".to_string()
        } else {
            format!("后端异常退出（代码 {}）", code)
        };
        let _ = app.emit("backend-error", message);
    }
    crate::tray::refresh(app);
}

fn cancel_start_timeout() {
    TIMEOUT_GENERATION.fetch_add(1, Ordering::SeqCst);
}

fn schedule_start_timeout(app: tauri::AppHandle) {
    let generation = TIMEOUT_GENERATION.fetch_add(1, Ordering::SeqCst) + 1;
    logger::log(
        "shell",
        format!(
            "backend start timeout scheduled, generation={}, timeout_ms={}",
            generation, START_TIMEOUT_MS
        ),
    );

    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(START_TIMEOUT_MS));

        if TIMEOUT_GENERATION.load(Ordering::SeqCst) != generation {
            logger::log(
                "shell",
                format!("backend start timeout ignored, generation={}", generation),
            );
            return;
        }

        let timed_out = with_state(|state| {
            if state.is_starting() {
                *state = BackendState::Failed(BackendError::Message(
                    "后端启动超时：15 秒内没有收到就绪信号。".to_string(),
                ));
                true
            } else {
                false
            }
        });

        if !timed_out {
            return;
        }

        logger::log("shell", "backend start timed out");
        let _ = app.emit("backend-error", "后端启动超时：15 秒内没有收到就绪信号。");
        crate::tray::refresh(&app);
    });
}

/// 从可执行文件目录向上查找仓库根（以 `src/desktopEntry.py` 为标志）。
fn find_repo_root(start: &std::path::Path) -> PathBuf {
    let mut current = start;
    loop {
        if current.join("src/desktopEntry.py").is_file() {
            return current.to_path_buf();
        }
        match current.parent() {
            Some(parent) => current = parent,
            None => return start.to_path_buf(),
        }
    }
}

/// 请求后端优雅停机：关闭 stdin → 等待 ≤timeout → 必要时强杀。
pub fn stop(timeout: Duration) {
    // 关闭 stdin：后端读到 EOF 后自行 graceful shutdown
    drop(
        BACKEND_STDIN
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .take(),
    );

    let pid = *BACKEND_PID
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let Some(pid) = pid else {
        return;
    };

    // 以「子进程已退出（handle_exit 清空 PID）」为准，而不是状态机：
    // 启动超时会把状态置为 Failed，但进程可能仍然存活。
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if BACKEND_PID
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .is_none()
        {
            return;
        }
        std::thread::sleep(Duration::from_millis(50));
    }

    logger::log(
        "shell",
        format!("backend did not exit in time, killing pid={}", pid),
    );
    kill_process(pid);
    *BACKEND_PID
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = None;
    std::thread::sleep(Duration::from_millis(300));
}

/// 托盘/主窗口「重试启动」。
pub fn restart(app: &tauri::AppHandle) -> Result<(), String> {
    if snapshot().is_running() {
        stop(Duration::from_secs(5));
    }
    reset_to_not_started();
    logger::log("shell", "restarting backend");
    crate::reload_splash_window(app);
    crate::launch_backend(app, false);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_error_marker_with_code() {
        assert_eq!(
            parse_error_marker("code=2 message=端口 8180 被占用"),
            (Some(2), "端口 8180 被占用".to_string())
        );
    }

    #[test]
    fn parses_error_marker_without_code() {
        assert_eq!(
            parse_error_marker("后端启动失败"),
            (None, "后端启动失败".to_string())
        );
        assert_eq!(
            parse_error_marker("code=abc message=bad code"),
            (None, "code=abc message=bad code".to_string())
        );
    }

    #[test]
    fn debug_snapshot_describes_states() {
        assert_eq!(BackendState::NotStarted.describe(), "NotStarted");
        assert_eq!(
            BackendState::Failed(BackendError::Message("boom".to_string())).describe(),
            "Failed(Message(boom))"
        );
    }
}

fn kill_process(pid: u32) {
    #[cfg(unix)]
    unsafe {
        libc::kill(pid as i32, libc::SIGKILL);
    }

    #[cfg(windows)]
    {
        let _ = std::process::Command::new("taskkill")
            .args(["/F", "/PID", &pid.to_string()])
            .output();
    }
}
