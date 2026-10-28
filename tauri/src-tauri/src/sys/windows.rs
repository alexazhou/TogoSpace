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
        let sidecar = resource_dir.join("backend/TogoSpace-backend.exe");
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

    let mut command = Command::new("clip");
    command.stdin(std::process::Stdio::piped());
    command.stdout(std::process::Stdio::null());
    command.stderr(std::process::Stdio::null());
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
