//! 系统托盘：菜单、告警状态与退出流程。

use crate::backend::{self, BackendState};
use crate::{logger, sys};
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager};

const TRAY_ID: &str = "togospace-tray";

// 图标编译进二进制：dev 与发布形态都不依赖资源目录布局
static TRAY_ICON: &[u8] = include_bytes!("../icons/tray-icon@2x.png");
static TRAY_ALERT_ICON: &[u8] = include_bytes!("../icons/tray-icon-alert@2x.png");

pub struct TrayState {
    normal_icon: Image<'static>,
    alert_icon: Image<'static>,
    error_item: MenuItem<tauri::Wry>,
    restart_item: MenuItem<tauri::Wry>,
}

fn backend_url(app: &AppHandle) -> String {
    app.state::<crate::BackendUrl>()
        .0
        .lock()
        .map(|value| value.clone())
        .unwrap_or_else(|_| String::new())
}

fn auth_token(app: &AppHandle) -> String {
    app.state::<crate::AuthToken>().0.clone()
}

pub fn build(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let version = app.package_info().version.to_string();

    let header = MenuItem::with_id(
        app,
        "header",
        format!("TogoSpace  v{}", version),
        false,
        None::<&str>,
    )?;
    let url_item = MenuItem::with_id(
        app,
        "backend_url",
        backend_url(app),
        false,
        None::<&str>,
    )?;
    let show_item = MenuItem::with_id(app, "show", "显示窗口", true, None::<&str>)?;
    let open_browser_item =
        MenuItem::with_id(app, "open_browser", "在浏览器中打开", true, None::<&str>)?;
    let copy_token_item =
        MenuItem::with_id(app, "copy_token", "复制访问 Token", true, None::<&str>)?;
    let open_config_item =
        MenuItem::with_id(app, "open_config", "打开配置目录", true, None::<&str>)?;
    let open_logs_item = MenuItem::with_id(app, "open_logs", "打开日志目录", true, None::<&str>)?;
    let error_item = MenuItem::with_id(app, "view_error", "查看错误", false, None::<&str>)?;
    let restart_item = MenuItem::with_id(app, "restart", "重试启动", false, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;

    let menu = Menu::with_items(
        app,
        &[
            &header,
            &url_item,
            &show_item,
            &open_browser_item,
            &copy_token_item,
            &error_item,
            &restart_item,
            &open_config_item,
            &open_logs_item,
            &quit_item,
        ],
    )?;

    let normal_icon = Image::from_bytes(TRAY_ICON)?;
    let alert_icon = Image::from_bytes(TRAY_ALERT_ICON)?;

    TrayIconBuilder::with_id(TRAY_ID)
        .icon(normal_icon.clone())
        .icon_as_template(true)
        .tooltip("TogoSpace")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => crate::show_main_window(app),
            "open_browser" => {
                let url = backend_url(app);
                if !url.is_empty() {
                    let _ = open::that(url);
                }
            }
            "copy_token" => {
                let token = auth_token(app);
                if !token.is_empty() {
                    sys::platform::copy_to_clipboard(&token);
                    logger::log("shell", "access token copied to clipboard");
                }
            }
            "open_config" => {
                let root = crate::config::storage_root();
                let _ = open::that(root);
            }
            "open_logs" => {
                let logs = crate::config::storage_root().join("logs");
                let _ = open::that(logs);
            }
            "view_error" => {
                if let Some(message) = backend::error_message() {
                    use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
                    app.dialog()
                        .message(message)
                        .title("TogoSpace")
                        .kind(MessageDialogKind::Error)
                        .show(|_| {});
                }
            }
            "restart" => {
                if let Err(error) = backend::restart(app) {
                    logger::log("shell", format!("restart backend failed: {}", error));
                }
            }
            "quit" => crate::request_quit(app),
            _ => {}
        })
        .build(app)?;

    app.manage(TrayState {
        normal_icon,
        alert_icon,
        error_item,
        restart_item,
    });

    Ok(())
}

/// 根据后端状态刷新托盘图标与菜单项可用性。
pub fn refresh(app: &AppHandle) {
    let Some(state) = app.try_state::<TrayState>() else {
        return;
    };

    let snapshot = backend::snapshot();
    let alert = matches!(snapshot, BackendState::Failed(_) | BackendState::Exited(_));

    let _ = state.error_item.set_enabled(alert && backend::error_message().is_some());
    let _ = state.restart_item.set_enabled(alert);

    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let icon = if alert {
            state.alert_icon.clone()
        } else {
            state.normal_icon.clone()
        };
        let _ = tray.set_icon(Some(icon));
        let _ = tray.set_icon_as_template(!alert);
        let _ = tray.set_tooltip(Some(if alert {
            "TogoSpace — 后端未运行"
        } else {
            "TogoSpace"
        }));
    }
}
