//! Menu bar presence.
//!
//! The rig runs all day, so the tray is the primary surface. The window is secondary and
//! stays hidden until the user asks for it.

use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, Runtime};

pub const OPEN: &str = "open";
pub const QUIT: &str = "quit";

pub fn build<R: Runtime>(app: &AppHandle<R>, status: &str) -> tauri::Result<()> {
    let status_item = MenuItem::with_id(app, "status", status, false, None::<&str>)?;
    let open_item = MenuItem::with_id(app, OPEN, "Open reviewrig", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, QUIT, "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &status_item,
            &PredefinedMenuItem::separator(app)?,
            &open_item,
            &PredefinedMenuItem::separator(app)?,
            &quit_item,
        ],
    )?;

    TrayIconBuilder::with_id("main")
        .icon(app.default_window_icon().cloned().expect("bundled icon"))
        .icon_as_template(true)
        .tooltip("reviewrig")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(on_menu_event)
        .build(app)?;
    Ok(())
}

// Tauri fixes this signature. The callback takes the event by value.
#[allow(clippy::needless_pass_by_value)]
fn on_menu_event<R: Runtime>(app: &AppHandle<R>, event: MenuEvent) {
    match event.id.as_ref() {
        OPEN => show_window(app),
        QUIT => app.exit(0),
        _ => {}
    }
}

pub fn show_window<R: Runtime>(app: &AppHandle<R>) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}
