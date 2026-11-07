//! 桌面端登录 shell PATH 读取。
//!
//! GUI 启动的 Tauri 外壳拿不到用户 shell 启动文件里的 `PATH`，而 sidecar 会继承同一份最小环境。
//! 本模块在应用启动时探测一次登录 shell 的 `PATH`，缓存后注入 Python sidecar。
//!
//! 设计文档：`docs/design/2026-11-08-desktop-login-shell-path/step2_technical.md`。

use std::ffi::{OsStr, OsString};
use std::process::Command;
use std::sync::OnceLock;

static CACHE: OnceLock<Option<OsString>> = OnceLock::new();

/// 返回登录 shell 的 `PATH`。
///
/// 首次调用会同步完成探测（受总预算约束）；允许阻塞调用线程，调用方应确保不在 Tauri 主线程上
/// 首次调用（生产路径由 `setup` 中的预热线程先触发）。
pub fn path() -> Option<&'static OsStr> {
    cached_path(&CACHE, resolve_path).as_deref()
}

/// 若探测到登录 shell 的 `PATH`，注入到 sidecar 命令。
pub fn inject_path(command: &mut Command) {
    apply_path(command, path());
}

fn apply_path(command: &mut Command, path: Option<&OsStr>) {
    if let Some(path) = path {
        command.env("PATH", path);
    }
}

/// 可注入的记忆化入口：生产使用进程级 `CACHE`，单测使用局部 `OnceLock` 验证只执行一次。
fn cached_path<T>(cache: &OnceLock<Option<T>>, init: impl FnOnce() -> Option<T>) -> &Option<T> {
    cache.get_or_init(init)
}

#[cfg(unix)]
use unix::resolve_path;

#[cfg(not(unix))]
fn resolve_path() -> Option<OsString> {
    // Windows 不读取登录 shell：沿用继承环境。
    None
}

#[cfg(unix)]
mod unix {
    use std::ffi::{CStr, OsString};
    use std::io::Read;
    use std::os::unix::ffi::OsStringExt;
    use std::os::unix::process::CommandExt;
    use std::path::{Path, PathBuf};
    use std::process::{Child, ChildStdout, Command, Stdio};
    use std::sync::mpsc::{self, SyncSender};
    use std::time::{Duration, Instant};

    use crate::logger;

    const TIMEOUT_ENV: &str = "TOGOSPACE_LOGIN_SHELL_TIMEOUT_MS";
    const PROBE_ENV: &str = "TOGOSPACE_LOGIN_SHELL_PROBE";

    const DEFAULT_TOTAL_BUDGET_MS: u64 = 5_000;
    const MIN_REMAINING_MS: u64 = 1_000;
    const PER_CANDIDATE_FLOOR_MS: u64 = 3_000;
    const MAX_BUFFER_BYTES: usize = 256 * 1024;
    const REAP_GRACE_MS: u64 = 100;
    const QUIT_POLL_MS: u64 = 50;

    const SYSTEM_SHELLS: [&str; 3] = ["/bin/zsh", "/bin/bash", "/bin/sh"];

    /// 单条 `printf` 同时输出首尾定界符与 `$PATH`；拆成两次会扩大并发写 stdout 的插入窗口。
    const PROBE_COMMAND: &str = "printf '\\0__TOGOSPACE_LOGIN_SHELL_PATH__\\0%s\\0__TOGOSPACE_LOGIN_SHELL_PATH__\\0' \"$PATH\"; exit";
    const MARKER: &[u8] = b"\0__TOGOSPACE_LOGIN_SHELL_PATH__\0";

    #[derive(Debug)]
    enum ProbeError {
        /// 输出里还没有凑齐首尾两个标记。
        Incomplete,
        /// 累积缓冲区超过上限仍未凑齐标记。
        OutputLimit,
        /// 输出被污染或不是合法 PATH。
        Invalid(String),
        /// 子进程拉起失败。
        Spawn(String),
        /// 单候选超时。
        Timeout,
        /// 应用正在退出。
        Quitting,
    }

    impl ProbeError {
        fn describe(&self) -> String {
            match self {
                ProbeError::Incomplete => "incomplete output".to_string(),
                ProbeError::OutputLimit => "output exceeded limit".to_string(),
                ProbeError::Invalid(reason) => format!("invalid or unframed PATH ({})", reason),
                ProbeError::Spawn(reason) => format!("spawn error ({})", reason),
                ProbeError::Timeout => "timeout".to_string(),
                ProbeError::Quitting => "quit requested".to_string(),
            }
        }
    }

    #[derive(Debug)]
    struct SanitizedPath {
        value: OsString,
        kept: usize,
        dropped: usize,
        deduped: usize,
    }

    /// 生产入口：读环境变量、账户信息，生成候选并执行探测。
    pub(super) fn resolve_path() -> Option<OsString> {
        let probe_env = std::env::var(PROBE_ENV).ok();
        if !should_probe(probe_env.as_deref(), cfg!(debug_assertions)) {
            logger::log(
                "shell",
                "desktop login shell: probing skipped in dev build (set TOGOSPACE_LOGIN_SHELL_PROBE=1 to enable)",
            );
            return None;
        }

        let total_budget = resolve_timeout_budget();
        let candidates = login_shell_candidates();
        resolve_path_from(&candidates, total_budget)
    }

    fn should_probe(raw_env: Option<&str>, is_debug: bool) -> bool {
        if !is_debug {
            // release 始终探测；调控只针对 tauri dev。
            return true;
        }
        matches!(raw_env.map(str::trim), Some("1") | Some("true"))
    }

    fn resolve_timeout_budget() -> Duration {
        let raw = std::env::var(TIMEOUT_ENV).ok();
        if let Some(value) = raw.as_deref() {
            if parse_timeout_ms(Some(value)).is_none() {
                logger::log(
                    "shell",
                    format!(
                        "desktop login shell: invalid {}={:?}, using default {}ms",
                        TIMEOUT_ENV, value, DEFAULT_TOTAL_BUDGET_MS
                    ),
                );
            }
        }
        parse_timeout_budget(raw.as_deref())
    }

    fn parse_timeout_budget(raw: Option<&str>) -> Duration {
        Duration::from_millis(parse_timeout_ms(raw).unwrap_or(DEFAULT_TOTAL_BUDGET_MS))
    }

    fn parse_timeout_ms(raw: Option<&str>) -> Option<u64> {
        raw.and_then(|value| value.trim().parse::<u64>().ok())
            .filter(|ms| (500..=30_000).contains(ms))
    }

    fn candidate_timeout(total: Duration, remaining: Duration) -> Option<Duration> {
        if remaining < Duration::from_millis(MIN_REMAINING_MS) {
            return None;
        }
        let floor = Duration::from_millis(PER_CANDIDATE_FLOOR_MS);
        let half = total / 2;
        Some(remaining.min(floor.max(half)))
    }

    fn login_shell_candidates() -> Vec<PathBuf> {
        let account = account_shell();
        if let Some(shell) = account.as_deref() {
            if !is_posix_shell(shell) {
                logger::log(
                    "shell",
                    format!(
                        "desktop login shell: skipping non-POSIX account shell {:?}",
                        shell
                    ),
                );
            }
        }
        login_shell_candidates_for(account.as_deref())
    }

    fn login_shell_candidates_for(account_shell: Option<&Path>) -> Vec<PathBuf> {
        let mut candidates: Vec<PathBuf> = Vec::new();

        if let Some(shell) = account_shell {
            if is_posix_shell(shell) {
                candidates.push(shell.to_path_buf());
            }
        }

        for shell in SYSTEM_SHELLS {
            let path = PathBuf::from(shell);
            if !candidates.iter().any(|candidate| candidate == &path) {
                candidates.push(path);
            }
        }

        candidates
    }

    fn is_posix_shell(path: &Path) -> bool {
        matches!(
            path.file_name().and_then(|name| name.to_str()),
            Some("zsh") | Some("bash") | Some("sh")
        )
    }

    /// 账户 shell 来自 `getpwuid_r` 的 `pw_shell`，不读 `$SHELL`。
    fn account_shell() -> Option<PathBuf> {
        passwd_entry().and_then(|entry| entry.shell)
    }

    fn account_home() -> Option<PathBuf> {
        passwd_entry().and_then(|entry| entry.home)
    }

    struct PasswdInfo {
        shell: Option<PathBuf>,
        home: Option<PathBuf>,
    }

    fn passwd_entry() -> Option<PasswdInfo> {
        let uid = unsafe { libc::getuid() };
        let mut buf_len = match unsafe { libc::sysconf(libc::_SC_GETPW_R_SIZE_MAX) } {
            value if value > 0 => value as usize,
            _ => 1024,
        };

        loop {
            let mut buf = vec![0 as libc::c_char; buf_len];
            let mut passwd: libc::passwd = unsafe { std::mem::zeroed() };
            let mut result: *mut libc::passwd = std::ptr::null_mut();

            let code = unsafe {
                libc::getpwuid_r(uid, &mut passwd, buf.as_mut_ptr(), buf.len(), &mut result)
            };

            if code == libc::ERANGE && buf_len < 1024 * 1024 {
                buf_len *= 2;
                continue;
            }
            if code != 0 || result.is_null() {
                return None;
            }

            return Some(PasswdInfo {
                shell: cstring_to_path(passwd.pw_shell),
                home: cstring_to_path(passwd.pw_dir),
            });
        }
    }

    fn cstring_to_path(ptr: *const libc::c_char) -> Option<PathBuf> {
        if ptr.is_null() {
            return None;
        }
        let bytes = unsafe { CStr::from_ptr(ptr) }.to_bytes();
        if bytes.is_empty() {
            return None;
        }
        Some(PathBuf::from(OsString::from_vec(bytes.to_vec())))
    }

    fn probe_home() -> PathBuf {
        if let Some(home) = std::env::var_os("HOME") {
            if !home.is_empty() {
                return PathBuf::from(home);
            }
        }
        account_home().unwrap_or_else(|| PathBuf::from("/"))
    }

    fn resolve_path_from(candidates: &[PathBuf], total_budget: Duration) -> Option<OsString> {
        let started = Instant::now();

        for shell in candidates {
            let remaining = total_budget.saturating_sub(started.elapsed());
            let Some(timeout) = candidate_timeout(total_budget, remaining) else {
                logger::log(
                    "shell",
                    "desktop login shell: remaining budget below 1000ms, stop trying",
                );
                break;
            };

            logger::log(
                "shell",
                format!(
                    "desktop login shell: probing {:?} (budget {}ms)",
                    shell,
                    timeout.as_millis()
                ),
            );

            match probe_shell(shell, timeout) {
                Ok(result) => {
                    logger::log(
                        "shell",
                        format!(
                            "desktop login shell: resolved PATH from {:?} (kept {} entries, dropped {} non-absolute, deduped {} duplicate entries)",
                            shell, result.kept, result.dropped, result.deduped
                        ),
                    );
                    return Some(result.value);
                }
                Err(ProbeError::Quitting) => {
                    logger::log("shell", "desktop login shell: quit requested, stop probing");
                    return None;
                }
                Err(error) => {
                    logger::log(
                        "shell",
                        format!(
                            "desktop login shell: candidate {:?} failed: {}",
                            shell,
                            error.describe()
                        ),
                    );
                }
            }
        }

        logger::log(
            "shell",
            "desktop login shell: all candidates failed, falling back to inherited PATH",
        );
        None
    }

    fn probe_shell(shell: &Path, timeout: Duration) -> Result<SanitizedPath, ProbeError> {
        if crate::is_quitting() {
            return Err(ProbeError::Quitting);
        }

        let mut command = Command::new(shell);
        command
            .arg("-ilc")
            .arg(PROBE_COMMAND)
            .current_dir(probe_home())
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .env("DISABLE_AUTO_UPDATE", "true")
            .env("ZSH_TMUX_AUTOSTARTED", "true")
            .env("ZSH_TMUX_AUTOSTART", "false");

        // setsid：脱离控制终端，且使 PGID == PID，超时可整组回收。
        unsafe {
            command.pre_exec(|| {
                if libc::setsid() == -1 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }

        let mut child = command
            .spawn()
            .map_err(|error| ProbeError::Spawn(error.to_string()))?;
        let pid = child.id();

        let Some(stdout) = child.stdout.take() else {
            kill_group(pid);
            let _ = child.wait();
            return Err(ProbeError::Spawn("stdout pipe unavailable".to_string()));
        };

        let (tx, rx) = mpsc::sync_channel(1);
        spawn_stdout_reader(stdout, tx);

        let deadline = Instant::now() + timeout;
        let result = loop {
            match rx.recv_timeout(Duration::from_millis(QUIT_POLL_MS)) {
                Ok(result) => break result,
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    if crate::is_quitting() {
                        kill_group(pid);
                        let _ = child.wait();
                        return Err(ProbeError::Quitting);
                    }
                    if Instant::now() >= deadline {
                        kill_group(pid);
                        let _ = child.wait();
                        return Err(ProbeError::Timeout);
                    }
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    kill_group(pid);
                    let _ = child.wait();
                    return Err(ProbeError::Invalid(
                        "reader exited without result".to_string(),
                    ));
                }
            }
        };

        let raw = match result {
            Ok(raw) => {
                let grace = Duration::from_millis(REAP_GRACE_MS)
                    .min(deadline.saturating_duration_since(Instant::now()));
                reap_with_grace(&mut child, pid, grace);
                raw
            }
            Err(error) => {
                kill_group(pid);
                let _ = child.wait();
                return Err(error);
            }
        };

        extract_framed_path(&raw)
    }

    fn spawn_stdout_reader(mut stdout: ChildStdout, tx: SyncSender<Result<Vec<u8>, ProbeError>>) {
        std::thread::spawn(move || {
            let mut buf: Vec<u8> = Vec::new();
            let mut chunk = [0u8; 8192];
            let mut done = false;

            loop {
                match stdout.read(&mut chunk) {
                    Ok(0) => break,
                    Ok(read) => {
                        if done {
                            continue;
                        }
                        buf.extend_from_slice(&chunk[..read]);
                        if extract_framed_region(&buf).is_ok() {
                            let _ = tx.send(Ok(buf.clone()));
                            done = true;
                            buf.clear();
                        } else if buf.len() > MAX_BUFFER_BYTES {
                            let _ = tx.send(Err(ProbeError::OutputLimit));
                            done = true;
                            buf.clear();
                        }
                    }
                    Err(ref error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
                    Err(error) => {
                        if !done {
                            let _ =
                                tx.send(Err(ProbeError::Invalid(format!("read error: {}", error))));
                        }
                        break;
                    }
                }
            }

            if !done {
                let _ = tx.send(Err(ProbeError::Invalid("unframed output".to_string())));
            }
        });
    }

    fn reap_with_grace(child: &mut Child, pid: u32, grace: Duration) {
        let deadline = Instant::now() + grace;
        loop {
            match child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) => {
                    if Instant::now() >= deadline {
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(10));
                }
                Err(_) => break,
            }
        }

        kill_group(pid);
        let _ = child.wait();
    }

    fn kill_group(pid: u32) {
        // 探针通过 setsid() 建立独立进程组（PGID == PID），负 pid 表示整个进程组。
        unsafe {
            libc::kill(-(pid as i32), libc::SIGKILL);
        }
    }

    fn extract_framed_path(raw: &[u8]) -> Result<SanitizedPath, ProbeError> {
        sanitize_path_bytes(extract_framed_region(raw)?)
    }

    fn extract_framed_region(raw: &[u8]) -> Result<&[u8], ProbeError> {
        let first = find_subslice(raw, MARKER).ok_or(ProbeError::Incomplete)?;
        let start = first + MARKER.len();
        let second = find_subslice(&raw[start..], MARKER).ok_or(ProbeError::Incomplete)?;
        Ok(&raw[start..start + second])
    }

    fn find_subslice(haystack: &[u8], needle: &[u8]) -> Option<usize> {
        if needle.is_empty() || haystack.len() < needle.len() {
            return None;
        }
        haystack
            .windows(needle.len())
            .position(|window| window == needle)
    }

    fn sanitize_path_bytes(raw: &[u8]) -> Result<SanitizedPath, ProbeError> {
        if raw.is_empty() {
            return Err(ProbeError::Invalid("empty PATH".to_string()));
        }
        if raw.iter().any(|byte| matches!(*byte, 0 | b'\r' | b'\n')) {
            return Err(ProbeError::Invalid("PATH contains NUL/CR/LF".to_string()));
        }

        let mut kept: Vec<&[u8]> = Vec::new();
        let mut dropped = 0usize;
        let mut deduped = 0usize;
        for segment in raw.split(|byte| *byte == b':') {
            if segment.is_empty() {
                continue;
            }
            if segment[0] != b'/' {
                dropped += 1;
                continue;
            }
            // 登录 shell 的 rc 常无条件追加同一路径（brew shellenv、orbstack init 等），
            // 按首次出现保序去重，避免 sidecar 与 Agent 子进程拿到重复条目。
            if kept.iter().any(|existing| *existing == segment) {
                deduped += 1;
                continue;
            }
            kept.push(segment);
        }

        if kept.is_empty() {
            return Err(ProbeError::Invalid("no absolute PATH entries".to_string()));
        }

        let mut joined: Vec<u8> = Vec::new();
        for (index, segment) in kept.iter().enumerate() {
            if index > 0 {
                joined.push(b':');
            }
            joined.extend_from_slice(segment);
        }

        Ok(SanitizedPath {
            value: OsString::from_vec(joined),
            kept: kept.len(),
            dropped,
            deduped,
        })
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use crate::login_shell::cached_path;
        use std::ffi::OsStr;
        use std::sync::atomic::{AtomicUsize, Ordering};
        use std::sync::OnceLock;

        // ── 超时预算 ────────────────────────────────────────────────────────

        #[test]
        fn parse_timeout_defaults_when_absent() {
            assert_eq!(
                parse_timeout_budget(None),
                Duration::from_millis(DEFAULT_TOTAL_BUDGET_MS)
            );
        }

        #[test]
        fn parse_timeout_accepts_valid_range() {
            assert_eq!(
                parse_timeout_budget(Some("10000")),
                Duration::from_millis(10_000)
            );
            assert_eq!(
                parse_timeout_budget(Some("500")),
                Duration::from_millis(500)
            );
            assert_eq!(
                parse_timeout_budget(Some("30000")),
                Duration::from_millis(30_000)
            );
            assert_eq!(
                parse_timeout_budget(Some(" 7000 ")),
                Duration::from_millis(7_000)
            );
        }

        #[test]
        fn parse_timeout_falls_back_on_invalid_values() {
            for raw in ["499", "30001", "abc", "", "10s"] {
                assert_eq!(
                    parse_timeout_budget(Some(raw)),
                    Duration::from_millis(DEFAULT_TOTAL_BUDGET_MS),
                    "raw={raw:?}"
                );
            }
        }

        #[test]
        fn candidate_timeout_uses_two_stage_budget() {
            assert_eq!(
                candidate_timeout(Duration::from_millis(5_000), Duration::from_millis(5_000)),
                Some(Duration::from_millis(3_000))
            );
            assert_eq!(
                candidate_timeout(Duration::from_millis(10_000), Duration::from_millis(10_000)),
                Some(Duration::from_millis(5_000))
            );
            assert_eq!(
                candidate_timeout(Duration::from_millis(5_000), Duration::from_millis(2_000)),
                Some(Duration::from_millis(2_000))
            );
        }

        #[test]
        fn candidate_timeout_stops_below_min_remaining() {
            assert_eq!(
                candidate_timeout(Duration::from_millis(5_000), Duration::from_millis(999)),
                None
            );
            assert_eq!(
                candidate_timeout(Duration::from_millis(5_000), Duration::from_millis(1_000)),
                Some(Duration::from_millis(1_000))
            );
        }

        // ── 探测开关 ────────────────────────────────────────────────────────

        #[test]
        fn should_probe_release_always_true() {
            assert!(should_probe(None, false));
            assert!(should_probe(Some("0"), false));
        }

        #[test]
        fn should_probe_debug_requires_opt_in() {
            assert!(!should_probe(None, true));
            assert!(!should_probe(Some("0"), true));
            assert!(should_probe(Some("1"), true));
            assert!(should_probe(Some("true"), true));
        }

        // ── 候选构造 ────────────────────────────────────────────────────────

        #[test]
        fn candidates_put_posix_account_shell_first_and_dedupe() {
            assert_eq!(
                login_shell_candidates_for(Some(Path::new("/bin/zsh"))),
                vec![
                    PathBuf::from("/bin/zsh"),
                    PathBuf::from("/bin/bash"),
                    PathBuf::from("/bin/sh"),
                ]
            );
            assert_eq!(
                login_shell_candidates_for(Some(Path::new("/bin/sh"))),
                vec![
                    PathBuf::from("/bin/sh"),
                    PathBuf::from("/bin/zsh"),
                    PathBuf::from("/bin/bash"),
                ]
            );
        }

        #[test]
        fn candidates_filter_non_posix_account_shell() {
            assert_eq!(
                login_shell_candidates_for(Some(Path::new("/opt/homebrew/bin/fish"))),
                vec![
                    PathBuf::from("/bin/zsh"),
                    PathBuf::from("/bin/bash"),
                    PathBuf::from("/bin/sh"),
                ]
            );
        }

        #[test]
        fn candidates_fall_back_to_system_shells_without_account() {
            let expected = vec![
                PathBuf::from("/bin/zsh"),
                PathBuf::from("/bin/bash"),
                PathBuf::from("/bin/sh"),
            ];
            assert_eq!(login_shell_candidates_for(None), expected);
            assert_eq!(
                login_shell_candidates_for(Some(Path::new(""))),
                expected,
                "空路径不应产生候选"
            );
        }

        // ── 定界符提取与 PATH 清洗 ──────────────────────────────────────────

        fn framed(path: &[u8]) -> Vec<u8> {
            let mut raw = Vec::new();
            raw.extend_from_slice(b"rc noise before\n");
            raw.extend_from_slice(MARKER);
            raw.extend_from_slice(path);
            raw.extend_from_slice(MARKER);
            raw.extend_from_slice(b"rc noise after");
            raw
        }

        #[test]
        fn extract_ignores_noise_around_markers() {
            let result = extract_framed_path(&framed(b"/opt/homebrew/bin:/usr/bin")).unwrap();
            assert_eq!(result.value, OsString::from("/opt/homebrew/bin:/usr/bin"));
            assert_eq!(result.kept, 2);
            assert_eq!(result.dropped, 0);
            assert_eq!(result.deduped, 0);
        }

        #[test]
        fn extract_rejects_single_marker_or_empty_output() {
            let mut single = Vec::new();
            single.extend_from_slice(MARKER);
            single.extend_from_slice(b"/usr/bin");
            assert!(extract_framed_path(&single).is_err());
            assert!(extract_framed_path(b"").is_err());
        }

        #[test]
        fn sanitize_rejects_control_bytes() {
            assert!(extract_framed_path(&framed(b"/usr/bin\n/bin")).is_err());
            assert!(extract_framed_path(&framed(b"/usr/bin\r/bin")).is_err());
            assert!(extract_framed_path(&framed(b"/usr/bin\0/bin")).is_err());
        }

        #[test]
        fn sanitize_drops_relative_entries_and_keeps_absolute() {
            let result = extract_framed_path(&framed(
                b".:./node_modules/.bin:/opt/homebrew/bin:/usr/bin:",
            ))
            .unwrap();
            assert_eq!(result.value, OsString::from("/opt/homebrew/bin:/usr/bin"));
            assert_eq!(result.kept, 2);
            assert_eq!(result.dropped, 2);
            assert_eq!(result.deduped, 0);
        }

        #[test]
        fn sanitize_dedupes_repeated_absolute_entries() {
            let result = extract_framed_path(&framed(
                b"/opt/homebrew/bin:/usr/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin",
            ))
            .unwrap();
            assert_eq!(
                result.value,
                OsString::from("/opt/homebrew/bin:/usr/bin:/usr/local/bin")
            );
            assert_eq!(result.kept, 3);
            assert_eq!(result.dropped, 0);
            assert_eq!(result.deduped, 2);
        }

        #[test]
        fn sanitize_rejects_all_relative_entries() {
            assert!(extract_framed_path(&framed(b".:./bin")).is_err());
            assert!(extract_framed_path(&framed(b":")).is_err());
        }

        #[test]
        fn sanitize_accepts_single_path_and_spaces() {
            let single = extract_framed_path(&framed(b"/usr/bin")).unwrap();
            assert_eq!(single.value, OsString::from("/usr/bin"));

            let spaced = extract_framed_path(&framed(
                b"/Applications/Visual Studio Code.app/Contents/Resources/app/bin",
            ))
            .unwrap();
            assert_eq!(
                spaced.value,
                OsString::from("/Applications/Visual Studio Code.app/Contents/Resources/app/bin")
            );
        }

        // ── 缓存与注入契约 ──────────────────────────────────────────────────

        #[test]
        fn cached_path_executes_init_only_once_for_some() {
            let cache = OnceLock::new();
            let calls = AtomicUsize::new(0);

            let first = cached_path(&cache, || {
                calls.fetch_add(1, Ordering::SeqCst);
                Some(OsString::from("/first"))
            });
            assert_eq!(first.as_deref(), Some(OsStr::new("/first")));

            let second = cached_path(&cache, || {
                calls.fetch_add(1, Ordering::SeqCst);
                Some(OsString::from("/second"))
            });
            assert_eq!(second.as_deref(), Some(OsStr::new("/first")));
            assert_eq!(calls.load(Ordering::SeqCst), 1);
        }

        #[test]
        fn cached_path_executes_init_only_once_for_none() {
            let cache = OnceLock::new();
            let calls = AtomicUsize::new(0);

            assert!(cached_path(&cache, || {
                calls.fetch_add(1, Ordering::SeqCst);
                None
            })
            .is_none());
            assert!(cached_path(&cache, || {
                calls.fetch_add(1, Ordering::SeqCst);
                Some(OsString::from("/late"))
            })
            .is_none());
            assert_eq!(calls.load(Ordering::SeqCst), 1);
        }

        #[test]
        fn apply_path_sets_env_when_present() {
            let mut command = Command::new("/bin/echo");
            super::super::apply_path(&mut command, Some(OsStr::new("/opt/homebrew/bin:/usr/bin")));

            let value = command
                .get_envs()
                .find(|(key, _)| *key == OsStr::new("PATH"))
                .and_then(|(_, value)| value);
            assert_eq!(value, Some(OsStr::new("/opt/homebrew/bin:/usr/bin")));
        }

        #[test]
        fn apply_path_does_nothing_when_absent() {
            let mut command = Command::new("/bin/echo");
            super::super::apply_path(&mut command, None);
            assert!(command.get_envs().all(|(key, _)| key != OsStr::new("PATH")));
        }

        // ── 进程级行为（spawn / 读取 / 回收）────────────────────────────────

        #[cfg(unix)]
        fn temp_script(name: &str, body: &str) -> PathBuf {
            use std::os::unix::fs::PermissionsExt;

            let dir = std::env::temp_dir().join(format!(
                "togospace-login-shell-test-{}-{}",
                std::process::id(),
                name
            ));
            std::fs::create_dir_all(&dir).expect("create temp dir");
            let path = dir.join("fake-shell");
            std::fs::write(&path, body).expect("write fake shell");
            let mut permissions = std::fs::metadata(&path).expect("stat").permissions();
            permissions.set_mode(0o755);
            std::fs::set_permissions(&path, permissions).expect("chmod");
            path
        }

        #[test]
        fn probe_shell_extracts_custom_path() {
            let script = temp_script(
                "success",
                "#!/bin/sh\nprintf '\\0__TOGOSPACE_LOGIN_SHELL_PATH__\\0%s\\0__TOGOSPACE_LOGIN_SHELL_PATH__\\0' '/opt/homebrew/bin:/usr/bin'\nexit 0\n",
            );

            let result = probe_shell(&script, Duration::from_millis(5_000)).expect("probe success");
            assert_eq!(result.value, OsString::from("/opt/homebrew/bin:/usr/bin"));
            assert_eq!(result.kept, 2);
            assert_eq!(result.dropped, 0);
            assert_eq!(result.deduped, 0);
        }

        #[test]
        fn resolve_path_from_falls_back_to_next_candidate() {
            let nonzero = temp_script("nonzero", "#!/bin/sh\nexit 1\n");
            let invalid = temp_script("invalid", "#!/bin/sh\nprintf '/usr/bin'\nexit 0\n");
            let good = temp_script(
                "fallback",
                "#!/bin/sh\nprintf '\\0__TOGOSPACE_LOGIN_SHELL_PATH__\\0%s\\0__TOGOSPACE_LOGIN_SHELL_PATH__\\0' '/usr/bin'\nexit 0\n",
            );
            let candidates = vec![
                PathBuf::from("/nonexistent/togospace-login-shell"),
                nonzero,
                invalid,
                good,
            ];

            // 预算放宽，避免并行测试负载下回退链耗尽 1000ms 下限。
            let result = resolve_path_from(&candidates, Duration::from_millis(10_000));
            assert_eq!(result, Some(OsString::from("/usr/bin")));
        }

        #[test]
        fn resolve_path_from_stops_when_remaining_budget_is_too_small() {
            let candidates = vec![
                PathBuf::from("/nonexistent/togospace-login-shell-1"),
                PathBuf::from("/nonexistent/togospace-login-shell-2"),
            ];

            let started = Instant::now();
            let result = resolve_path_from(&candidates, Duration::from_millis(500));
            assert!(result.is_none());
            assert!(
                started.elapsed() < Duration::from_secs(5),
                "budget-exhausted probe took too long: {:?}",
                started.elapsed()
            );
        }

        #[test]
        fn probe_shell_rejects_output_over_buffer_limit() {
            // 无定界符的 300 KiB 输出应在 256 KiB 处触发 OutputLimit，而不是等 EOF。
            let script = temp_script(
                "output-limit",
                "#!/bin/sh\nyes x | head -c 300000\nexit 0\n",
            );

            let started = Instant::now();
            let err = probe_shell(&script, Duration::from_millis(10_000)).unwrap_err();
            assert!(matches!(err, ProbeError::OutputLimit));
            assert!(
                started.elapsed() < Duration::from_secs(5),
                "output-limit probe took too long: {:?}",
                started.elapsed()
            );
        }

        #[test]
        fn probe_shell_reports_spawn_error() {
            let err = probe_shell(
                Path::new("/nonexistent/togospace-login-shell"),
                Duration::from_millis(500),
            )
            .unwrap_err();
            assert!(matches!(err, ProbeError::Spawn(_)));
        }

        #[test]
        fn probe_shell_returns_without_waiting_for_stdout_eof() {
            // 后台子进程持有 stdout；探测必须在第二个定界符出现后立刻返回。
            let script = temp_script(
                "detached-drain",
                "#!/bin/sh\nprintf '\\0__TOGOSPACE_LOGIN_SHELL_PATH__\\0%s\\0__TOGOSPACE_LOGIN_SHELL_PATH__\\0' '/usr/bin'\nsleep 10 &\nexit 0\n",
            );

            let started = Instant::now();
            let result = probe_shell(&script, Duration::from_millis(5_000)).expect("probe success");
            assert_eq!(result.value, OsString::from("/usr/bin"));
            assert!(
                started.elapsed() < Duration::from_secs(5),
                "probe waited for stdout EOF: {:?}",
                started.elapsed()
            );
        }

        #[test]
        fn probe_shell_times_out_and_reaps_process_group() {
            let script = temp_script("timeout", "#!/bin/sh\nsleep 5\n");

            let started = Instant::now();
            let err = probe_shell(&script, Duration::from_millis(300)).unwrap_err();
            assert!(matches!(err, ProbeError::Timeout));
            assert!(
                started.elapsed() < Duration::from_secs(5),
                "timeout probe took too long: {:?}",
                started.elapsed()
            );
        }
    }
}

#[cfg(all(test, not(unix)))]
mod not_unix_tests {
    #[test]
    fn path_returns_none_on_non_unix() {
        assert!(super::path().is_none());
    }
}
