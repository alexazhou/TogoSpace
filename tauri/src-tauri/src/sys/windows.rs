use std::path::Path;
use std::process::Command;

pub struct WindowsPlatformState {
    pub stdin: Option<std::process::ChildStdin>,
}
pub type PlatformState = WindowsPlatformState;

pub fn get_command(resource_dir: &Path, repo_root: &Path) -> Result<Command, String> {
    #[cfg(debug_assertions)]
    {
        let python = repo_root.join(".venv/Scripts/python.exe");
        let entry = repo_root.join("src/desktopEntry.py");
        if !python.exists() {
            return Err(format!("开发模式 Python 不存在: {:?}", python));
        }
        let mut command = Command::new(python);
        command.arg(entry).current_dir(repo_root);
        Ok(command)
    }

    #[cfg(not(debug_assertions))]
    {
        let _ = repo_root;
        let sidecar = resource_dir.join("backend/Togobot-backend.exe");
        if !sidecar.exists() {
            return Err(format!("后端可执行文件不存在: {:?}", sidecar));
        }
        Ok(Command::new(sidecar))
    }
}

/// 为后端进程配置 stdio 管道并隐藏控制台窗口。
pub fn setup_command(cmd: &mut Command) -> PlatformState {
    use std::os::windows::process::CommandExt;

    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());
    cmd.stdin(std::process::Stdio::piped());
    cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW

    WindowsPlatformState { stdin: None }
}

pub fn post_spawn(state: &mut PlatformState, child: &mut std::process::Child) {
    state.stdin = child.stdin.take();
}

pub fn take_stdin(state: &mut PlatformState) -> Option<std::process::ChildStdin> {
    state.stdin.take()
}

pub fn copy_to_clipboard(text: &str) {
    use std::io::Write;
    use std::os::windows::process::CommandExt;

    let mut command = Command::new("clip");
    command.stdin(std::process::Stdio::piped());
    command.stdout(std::process::Stdio::null());
    command.stderr(std::process::Stdio::null());
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    if let Ok(mut child) = command.spawn() {
        if let Some(mut stdin) = child.stdin.take() {
            let _ = stdin.write_all(text.as_bytes());
        }
        let _ = child.wait();
    }
}

pub fn set_dock_visibility(_app: &tauri::AppHandle, _visible: bool) {}

pub fn handle_run_event(_app: &tauri::AppHandle, _event: &tauri::RunEvent) -> bool {
    false
}

/// 无边框窗口创建后的初始化：去掉 DWM 残留边框，并按默认深色主题绘制。
///
/// 前端挂载后会调用 [`set_titlebar_theme`] 按实际主题纠正。
pub fn init_window_style(window: &tauri::WebviewWindow) {
    use windows_sys::Win32::Foundation::HWND;
    use windows_sys::Win32::Graphics::Dwm::{
        DwmSetWindowAttribute, DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE, DWMWA_USE_IMMERSIVE_DARK_MODE,
    };

    let Ok(hwnd) = window.hwnd() else {
        return;
    };
    let hwnd = hwnd.0 as isize as HWND;
    let dark_mode: i32 = 1;

    unsafe {
        DwmSetWindowAttribute(
            hwnd,
            DWMWA_BORDER_COLOR as u32,
            &DWMWA_COLOR_NONE as *const u32 as *const core::ffi::c_void,
            std::mem::size_of::<u32>() as u32,
        );
        DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE as u32,
            &dark_mode as *const i32 as *const core::ffi::c_void,
            std::mem::size_of::<i32>() as u32,
        );
    }
}

/// 把应用主题色同步到 Windows 无边框窗口的 DWM 属性。
///
/// 窗口没有原生标题栏，`DWMWA_BORDER_COLOR = DWMWA_COLOR_NONE` 用来去掉 DWM 残留边框；
/// `DWMWA_USE_IMMERSIVE_DARK_MODE` 同步深浅色，保证系统阴影/边框的观感一致。
/// `DWMWA_CAPTION_COLOR` / `DWMWA_TEXT_COLOR` 在没有原生标题栏时不直接可见，一并设置作为兜底。
///
/// Windows 10 不支持部分属性，`DwmSetWindowAttribute` 返回错误时保持系统默认外观；
/// 颜色解析失败同样直接跳过，避免影响窗口显示。
pub fn set_titlebar_theme(
    window: &tauri::WebviewWindow,
    background: &str,
    foreground: &str,
    dark: bool,
) {
    use windows_sys::Win32::Foundation::HWND;
    use windows_sys::Win32::Graphics::Dwm::{
        DwmSetWindowAttribute, DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_COLOR_NONE,
        DWMWA_TEXT_COLOR, DWMWA_USE_IMMERSIVE_DARK_MODE,
    };

    let Ok(hwnd) = window.hwnd() else {
        return;
    };
    let hwnd = hwnd.0 as isize as HWND;
    let (Some(background), Some(foreground)) =
        (parse_hex_color(background), parse_hex_color(foreground))
    else {
        return;
    };

    unsafe fn set_dword(hwnd: HWND, attribute: i32, value: u32) {
        DwmSetWindowAttribute(
            hwnd,
            attribute as u32,
            &value as *const u32 as *const core::ffi::c_void,
            std::mem::size_of::<u32>() as u32,
        );
    }

    unsafe {
        set_dword(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, u32::from(dark));
        set_dword(hwnd, DWMWA_CAPTION_COLOR, background);
        set_dword(hwnd, DWMWA_TEXT_COLOR, foreground);
        set_dword(hwnd, DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE);
    }
}

/// `#RRGGBB` → Windows COLORREF（`0x00BBGGRR`，注意是 BGR 顺序）。
fn parse_hex_color(value: &str) -> Option<u32> {
    let hex = value.strip_prefix('#')?;
    if hex.len() != 6 {
        return None;
    }
    let rgb = u32::from_str_radix(hex, 16).ok()?;
    let (r, g, b) = ((rgb >> 16) & 0xff, (rgb >> 8) & 0xff, rgb & 0xff);
    Some((b << 16) | (g << 8) | r)
}
