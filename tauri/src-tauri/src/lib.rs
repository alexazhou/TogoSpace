mod backend;
mod config;
mod logger;
mod sys;
mod tray;
pub mod utils;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use tauri::webview::PageLoadEvent;
use tauri::{Manager, WindowEvent};

use backend::{BackendLaunchConfig, BackendState};

#[derive(Debug, Clone, Copy)]
struct StartupGate {
    setup_finished: bool,
    splash_loaded: bool,
}

impl StartupGate {
    fn is_ready(&self) -> bool {
        self.setup_finished && self.splash_loaded
    }
}

static STARTUP_GATE: Mutex<StartupGate> = Mutex::new(StartupGate {
    setup_finished: false,
    splash_loaded: false,
});
static QUITTING: AtomicBool = AtomicBool::new(false);

fn with_gate<T>(f: impl FnOnce(&mut StartupGate) -> T) -> T {
    let mut gate = STARTUP_GATE
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    f(&mut gate)
}

fn gate_snapshot() -> StartupGate {
    STARTUP_GATE
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .to_owned()
}

pub(crate) fn is_quitting() -> bool {
    QUITTING.load(Ordering::SeqCst)
}

fn to_box_error(message: String) -> Box<dyn std::error::Error> {
    Box::new(std::io::Error::new(
        std::io::ErrorKind::Other,
        message,
    ))
}

/// 后端实际使用的 URL，前端通过 Tauri 命令查询。
pub struct BackendUrl(pub Mutex<String>);

/// 桌面会话 token，前端通过 Tauri 命令获取（只在 WebView 内存中持有）。
pub struct AuthToken(pub String);

fn show_splash_window(app: &tauri::AppHandle) -> Result<(), String> {
    if let Some(splash) = app.get_webview_window("splashscreen") {
        splash.show().map_err(|e| e.to_string())?;
        let _ = splash.set_focus();
        logger::log("shell", "splashscreen shown");
        Ok(())
    } else {
        Err("splashscreen 窗口不存在".to_string())
    }
}

pub(crate) fn reload_splash_window(app: &tauri::AppHandle) {
    if let Some(splash) = app.get_webview_window("splashscreen") {
        let _ = splash.reload();
        let _ = splash.show();
        let _ = splash.set_focus();
    }
}

fn close_splash_window(app: &tauri::AppHandle) {
    if let Some(splash) = app.get_webview_window("splashscreen") {
        let _ = splash.close();
    }
}

/// 后端就绪：关闭启动画面，打开主窗口。
pub(crate) fn on_backend_ready(app: &tauri::AppHandle) {
    close_splash_window(app);
    show_main_window(app);
    tray::refresh(app);
}

pub(crate) fn show_main_window(app: &tauri::AppHandle) {
    logger::log("shell", "show_main_window");
    sys::platform::set_dock_visibility(app, true);

    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }

    let result = tauri::WebviewWindowBuilder::new(
        app,
        "main",
        tauri::WebviewUrl::App("index.html".into()),
    )
    .title("TogoSpace")
    .inner_size(1280.0, 800.0)
    .min_inner_size(960.0, 600.0)
    .resizable(true)
    .build();

    match result {
        Ok(_) => logger::log("shell", "main window created"),
        Err(error) => logger::log("shell", format!("create main window failed: {:?}", error)),
    }
}

/// 启动后端（NotStarted → Starting）。
///
/// `ensure_splash` 为 true 时顺带显示启动画面；重试启动时会先重载启动画面。
pub(crate) fn launch_backend(app: &tauri::AppHandle, ensure_splash: bool) {
    if !backend::transition_to_starting() {
        logger::log(
            "shell",
            format!("backend start skipped, state={}", backend::snapshot().describe()),
        );
        return;
    }

    if ensure_splash {
        if let Err(error) = show_splash_window(app) {
            backend::fail(app, error);
            return;
        }
    }

    let handle = app.clone();
    std::thread::spawn(move || {
        if let Err(error) = backend::start(&handle) {
            backend::fail(&handle, error);
        }
    });
}

/// 启动画面加载完成且 setup 完成后才允许拉起后端。
fn maybe_start_backend_after_splash_load(app: &tauri::AppHandle) {
    let gate = gate_snapshot();
    if !gate.is_ready() {
        if !gate.splash_loaded {
            logger::log("shell", "backend start deferred, splash page not loaded yet");
        }
        if !gate.setup_finished {
            logger::log("shell", "backend start deferred, app setup not finished yet");
        }
        return;
    }

    if !matches!(backend::snapshot(), BackendState::NotStarted) {
        return;
    }

    if let Err(error) = show_splash_window(app) {
        backend::fail(app, error);
        return;
    }

    logger::log("shell", "backend launch starting");
    launch_backend(app, false);
}

/// 退出应用：先让后端优雅停机（≤5s），必要时强杀，再退出外壳。
pub(crate) fn request_quit(app: &tauri::AppHandle) {
    if QUITTING.swap(true, Ordering::SeqCst) {
        return;
    }
    logger::log("shell", "quit requested, stopping backend");

    let handle = app.clone();
    std::thread::spawn(move || {
        backend::stop(Duration::from_secs(5));
        handle.exit(0);
    });
}

// ── Tauri 命令（IPC）────────────────────────────────────────────────────────

#[tauri::command]
fn get_backend_url(state: tauri::State<BackendUrl>) -> String {
    state
        .0
        .lock()
        .map(|value| value.clone())
        .unwrap_or_default()
}

#[tauri::command]
fn get_auth_token(state: tauri::State<AuthToken>) -> String {
    state.0.clone()
}

#[tauri::command]
fn get_backend_state() -> String {
    backend::state_label().to_string()
}

#[tauri::command]
fn get_backend_error() -> Option<String> {
    backend::error_message()
}

#[tauri::command]
fn restart_backend(app: tauri::AppHandle) -> Result<(), String> {
    backend::restart(&app)
}

#[tauri::command]
fn open_external(url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return Err("仅支持 http/https 链接".to_string());
    }
    open::that(&url).map_err(|e| format!("打开链接失败: {}", e))
}

#[tauri::command]
async fn save_text_file(
    app: tauri::AppHandle,
    default_name: String,
    content: String,
) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;

    let (sender, receiver) = tokio::sync::oneshot::channel();
    app.dialog()
        .file()
        .set_file_name(&default_name)
        .save_file(move |path| {
            let _ = sender.send(path);
        });

    let selected = receiver
        .await
        .map_err(|e| format!("保存对话框失败: {}", e))?;
    let Some(file_path) = selected else {
        return Ok(None);
    };

    let path = file_path
        .into_path()
        .map_err(|e| format!("无效的保存路径: {}", e))?;
    std::fs::write(&path, content).map_err(|e| format!("写入文件失败: {}", e))?;
    Ok(Some(path.to_string_lossy().to_string()))
}

#[tauri::command]
fn exit_app(app: tauri::AppHandle) {
    request_quit(&app);
}

#[tauri::command]
fn log_to_rust(msg: String) {
    logger::log("frontend", msg);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // 单实例必须最先注册：第二次启动时把已有窗口唤到前台
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if backend::is_ready() {
                show_main_window(app);
            } else if let Some(splash) = app.get_webview_window("splashscreen") {
                let _ = splash.show();
                let _ = splash.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            get_backend_url,
            get_auth_token,
            get_backend_state,
            get_backend_error,
            restart_backend,
            open_external,
            save_text_file,
            exit_app,
            log_to_rust,
        ])
        .on_page_load(|webview, payload| {
            if webview.label() != "splashscreen" {
                return;
            }
            match payload.event() {
                PageLoadEvent::Started => {
                    logger::log("shell", format!("splash load started, url={}", payload.url()));
                }
                PageLoadEvent::Finished => {
                    logger::log("shell", format!("splash load finished, url={}", payload.url()));
                    with_gate(|gate| gate.splash_loaded = true);
                    maybe_start_backend_after_splash_load(webview.app_handle());
                }
            }
        })
        .setup(|app| {
            let storage_root = config::storage_root();
            std::fs::create_dir_all(&storage_root)?;
            let log_dir = storage_root.join("logs").join("desktop");
            if let Err(error) = logger::init(&log_dir) {
                eprintln!("logger init failed: {}", error);
            }
            logger::log(
                "shell",
                format!("setup started, storage_root={:?}", storage_root),
            );

            let mut desktop = config::load_or_create(&storage_root).map_err(to_box_error)?;
            let selected_port = config::choose_port(desktop.port).map_err(to_box_error)?;
            if selected_port != desktop.port {
                logger::log(
                    "shell",
                    format!(
                        "port {} occupied, falling back to {}",
                        desktop.port, selected_port
                    ),
                );
                desktop.port = selected_port;
                config::save(&storage_root, &desktop).map_err(to_box_error)?;
            }

            let backend_url = utils::generate_client_url("127.0.0.1", desktop.port);
            logger::log(
                "shell",
                format!(
                    "desktop config: port={}, backend_url={}",
                    desktop.port, backend_url
                ),
            );

            app.manage(BackendLaunchConfig {
                storage_root: storage_root.clone(),
                port: desktop.port,
                token: desktop.token.clone(),
            });
            app.manage(BackendUrl(Mutex::new(backend_url)));
            app.manage(AuthToken(desktop.token));

            tray::build(app.handle())?;

            with_gate(|gate| gate.setup_finished = true);
            maybe_start_backend_after_splash_load(app.handle());
            logger::log("shell", "setup finished");
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // 主窗口关闭 = 隐藏到托盘，不退出应用
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                    sys::platform::set_dock_visibility(window.app_handle(), false);
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // 平台特有事件（如 macOS 点 Dock 图标重新打开）由 sys 层处理
            if sys::platform::handle_run_event(app_handle, &event) {
                return;
            }

            match event {
                tauri::RunEvent::ExitRequested { api, .. } => {
                    // Cmd+Q / 菜单退出：走优雅停机；其余关窗场景不退出应用
                    if is_quitting() {
                        return;
                    }
                    api.prevent_exit();
                    request_quit(app_handle);
                }
                _ => {}
            }
        });
}
