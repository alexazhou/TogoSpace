//! `desktop.json` 读写与端口选择。
//!
//! 职责分离：Rust 只碰 `desktop.json`，Python 只碰 `setting.json`，
//! 避免两个进程写同一文件。

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::net::TcpListener;
use std::path::{Path, PathBuf};

pub const DEFAULT_PORT: u16 = 8180;
const CONFIG_FILE_NAME: &str = "desktop.json";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DesktopConfig {
    pub port: u16,
    pub token: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_ready_at: Option<String>,
}

impl DesktopConfig {
    fn with_defaults() -> Self {
        Self {
            port: DEFAULT_PORT,
            token: String::new(),
            last_ready_at: None,
        }
    }
}

/// 可写数据目录：与 Python 侧 appPaths 保持一致（环境变量优先）。
pub fn storage_root() -> PathBuf {
    if let Ok(value) = std::env::var("STORAGE_ROOT") {
        let trimmed = value.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }
    home_dir().join(".togospace")
}

fn home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("USERPROFILE").map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."))
}

pub fn config_path(storage_root: &Path) -> PathBuf {
    storage_root.join(CONFIG_FILE_NAME)
}

/// 读取配置；缺失或非法项回落到默认值并重写文件。
pub fn load_or_create(storage_root: &Path) -> Result<DesktopConfig, String> {
    fs::create_dir_all(storage_root)
        .map_err(|e| format!("创建数据目录失败 {:?}: {}", storage_root, e))?;

    let path = config_path(storage_root);
    let mut config = DesktopConfig::with_defaults();
    let mut need_write = false;

    if path.exists() {
        match fs::read_to_string(&path) {
            Ok(content) => match serde_json::from_str::<serde_json::Value>(&content) {
                Ok(json) => {
                    match json.get("port").and_then(|value| value.as_u64()) {
                        Some(port) if (1..=65535).contains(&port) => config.port = port as u16,
                        Some(_) | None => need_write = true,
                    }
                    match json.get("token").and_then(|value| value.as_str()) {
                        Some(token) if !token.trim().is_empty() => {
                            config.token = token.to_string();
                        }
                        Some(_) | None => need_write = true,
                    }
                    config.last_ready_at = json
                        .get("last_ready_at")
                        .and_then(|value| value.as_str())
                        .map(str::to_string);
                }
                // 非法 JSON：整体回落默认值
                Err(_) => need_write = true,
            },
            Err(_) => need_write = true,
        }
    } else {
        need_write = true;
    }

    if config.token.is_empty() {
        config.token = uuid::Uuid::new_v4().to_string();
        need_write = true;
    }

    if need_write {
        save(storage_root, &config)?;
    }
    Ok(config)
}

/// 原子落盘（临时文件 + rename），Unix 下权限收紧到 0600。
pub fn save(storage_root: &Path, config: &DesktopConfig) -> Result<(), String> {
    let path = config_path(storage_root);
    let tmp_path = path.with_extension("json.tmp");
    let content = serde_json::to_string_pretty(config)
        .map_err(|e| format!("序列化 desktop.json 失败: {}", e))?;

    let mut options = fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }

    let mut file = options
        .open(&tmp_path)
        .map_err(|e| format!("写入 desktop.json 失败 {:?}: {}", tmp_path, e))?;
    file.write_all(content.as_bytes())
        .map_err(|e| format!("写入 desktop.json 失败: {}", e))?;
    file.write_all(b"\n")
        .map_err(|e| format!("写入 desktop.json 失败: {}", e))?;
    drop(file);

    fs::rename(&tmp_path, &path)
        .map_err(|e| format!("保存 desktop.json 失败 {:?}: {}", path, e))?;
    Ok(())
}

/// 记录最近一次就绪时间（unix 秒），失败不影响启动。
pub fn record_ready(storage_root: &Path, config: &mut DesktopConfig) {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0);
    config.last_ready_at = Some(now.to_string());
    let _ = save(storage_root, config);
}

/// 选择监听端口：优先复用上次端口，被占用时退回系统分配的空闲端口。
pub fn choose_port(preferred: u16) -> Result<u16, String> {
    if TcpListener::bind(("127.0.0.1", preferred)).is_ok() {
        return Ok(preferred);
    }

    let listener = TcpListener::bind(("127.0.0.1", 0))
        .map_err(|e| format!("无法分配空闲端口: {}", e))?;
    let port = listener
        .local_addr()
        .map_err(|e| format!("读取空闲端口失败: {}", e))?
        .port();
    drop(listener);
    Ok(port)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "togospace-config-test-{}-{}",
            name,
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    #[test]
    fn creates_config_with_default_port_and_token() {
        let dir = temp_dir("create");
        let config = load_or_create(&dir).expect("load config");

        assert_eq!(config.port, DEFAULT_PORT);
        assert!(!config.token.is_empty());
        assert!(config_path(&dir).exists());

        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn repairs_invalid_port_and_missing_token() {
        let dir = temp_dir("repair");
        fs::write(config_path(&dir), r#"{"port": 70000, "token": "  "}"#).unwrap();

        let config = load_or_create(&dir).expect("load config");

        assert_eq!(config.port, DEFAULT_PORT);
        assert!(!config.token.is_empty());

        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn keeps_valid_values() {
        let dir = temp_dir("keep");
        fs::write(config_path(&dir), r#"{"port": 9123, "token": "fixed-token"}"#).unwrap();

        let config = load_or_create(&dir).expect("load config");

        assert_eq!(config.port, 9123);
        assert_eq!(config.token, "fixed-token");

        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn record_ready_persists_timestamp() {
        let dir = temp_dir("ready");
        let mut config = load_or_create(&dir).expect("load config");

        record_ready(&dir, &mut config);

        let reloaded = load_or_create(&dir).expect("reload config");
        assert!(reloaded.last_ready_at.is_some());

        fs::remove_dir_all(&dir).ok();
    }
}
