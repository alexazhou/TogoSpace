use std::path::Path;
use std::process::Command;

pub type PlatformState = crate::sys::unix::UnixPlatformState;

pub fn get_command(resource_dir: &Path, repo_root: &Path) -> Result<Command, String> {
    #[cfg(debug_assertions)]
    {
        let python = repo_root.join(".venv/bin/python3");
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

pub fn set_dock_visibility(_app: &tauri::AppHandle, _visible: bool) {}

pub fn handle_run_event(_app: &tauri::AppHandle, _event: &tauri::RunEvent) -> bool {
    false
}
