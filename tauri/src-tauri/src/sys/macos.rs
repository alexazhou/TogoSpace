use std::path::Path;
use std::process::Command;

pub type PlatformState = crate::sys::unix::UnixPlatformState;

/// 构造后端启动命令。
///
/// - 开发（`tauri dev`，debug 构建）：直接运行仓库 `.venv` 里的 Python 入口
/// - 发布：运行 `Resources/backend/TogoSpace-backend`
pub fn get_command(resource_dir: &Path, repo_root: &Path) -> Result<Command, String> {
    #[cfg(debug_assertions)]
    {
        let _ = resource_dir;
        let python = repo_root.join(".venv/bin/python3");
        let entry = repo_root.join("src/desktopEntry.py");
        if !python.exists() {
            return Err(format!("开发模式 Python 不存在: {:?}", python));
        }
        if !entry.exists() {
            return Err(format!("桌面后端入口不存在: {:?}", entry));
        }
        let mut command = Command::new(python);
        command.arg(entry).current_dir(repo_root);
        Ok(command)
    }

    #[cfg(not(debug_assertions))]
    {
        let _ = repo_root;
        let sidecar = resource_dir.join("backend/TogoSpace-backend");
        if !sidecar.exists() {
            return Err(format!("后端可执行文件不存在: {:?}", sidecar));
        }
        Ok(Command::new(sidecar))
    }
}

pub fn setup_command(cmd: &mut Command) -> PlatformState {
    crate::sys::unix::setup_command(cmd)
}

pub fn post_spawn(state: &mut PlatformState, child: &mut std::process::Child) {
    crate::sys::unix::post_spawn(state, child)
}

pub fn take_stdin(state: &mut PlatformState) -> Option<std::process::ChildStdin> {
    crate::sys::unix::take_stdin(state)
}

pub fn copy_to_clipboard(text: &str) {
    crate::sys::unix::copy_to_clipboard(text)
}

pub fn set_dock_visibility(app: &tauri::AppHandle, visible: bool) {
    let policy = if visible {
        tauri::ActivationPolicy::Regular
    } else {
        tauri::ActivationPolicy::Accessory
    };

    if let Err(error) = app.set_activation_policy(policy) {
        crate::logger::log(
            "shell",
            format!("设置 Dock 可见性失败 (visible={}): {:?}", visible, error),
        );
    }
}

/// 处理平台特有事件；返回 true 表示事件已由平台层处理。
///
/// macOS 独有：点击 Dock 图标重新打开应用。
pub fn handle_run_event(app: &tauri::AppHandle, event: &tauri::RunEvent) -> bool {
    match event {
        tauri::RunEvent::Reopen {
            has_visible_windows, ..
        } => {
            if !*has_visible_windows {
                if crate::backend::is_ready() {
                    crate::show_main_window(app);
                } else if let Some(splash) = tauri::Manager::get_webview_window(app, "splashscreen") {
                    let _ = splash.show();
                    let _ = splash.set_focus();
                }
            }
            true
        }
        _ => false,
    }
}
