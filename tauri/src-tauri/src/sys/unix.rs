use std::io::Write;
use std::process::{ChildStdin, Command, Stdio};

pub struct UnixPlatformState {
    pub stdin: Option<ChildStdin>,
}

/// 为后端进程配置 stdio 管道。三路各自的归属：
///   - stdin  → `post_spawn` 收进 PlatformState，作为父进程存活探针
///   - stdout / stderr → `utils::watch_piped_stdio` 一并接管（stdout 驱动启动状态机，stderr 只记日志）
///
/// 被接成管道的 stdio 必须有读取者：无人读取时，子进程写满缓冲区后写端会被阻塞。
pub fn setup_command(cmd: &mut Command) -> UnixPlatformState {
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    cmd.stdin(Stdio::piped());
    UnixPlatformState { stdin: None }
}

pub fn post_spawn(state: &mut UnixPlatformState, child: &mut std::process::Child) {
    // 接管子进程的 stdin，随 Tauri 进程存活。Tauri 退出或崩溃时被释放，
    // 子进程 stdin 管道收到 EOF，从而自行优雅退出（不留孤儿进程）。
    state.stdin = child.stdin.take();
}

pub fn take_stdin(state: &mut UnixPlatformState) -> Option<ChildStdin> {
    state.stdin.take()
}

/// 用系统剪贴板命令写入文本（不引入额外 Tauri 插件）。
pub fn copy_to_clipboard(text: &str) {
    #[cfg(target_os = "macos")]
    let candidates: &[(&str, &[&str])] = &[("pbcopy", &[])];

    #[cfg(not(target_os = "macos"))]
    let candidates: &[(&str, &[&str])] = &[
        ("wl-copy", &[]),
        ("xclip", &["-selection", "clipboard"]),
        ("xsel", &["--clipboard", "--input"]),
    ];

    for (program, args) in candidates {
        let mut command = Command::new(program);
        command.args(*args);
        command.stdin(Stdio::piped());
        command.stdout(Stdio::null());
        command.stderr(Stdio::null());

        if let Ok(mut child) = command.spawn() {
            if let Some(mut stdin) = child.stdin.take() {
                let _ = stdin.write_all(text.as_bytes());
            }
            let _ = child.wait();
            return;
        }
    }
}
