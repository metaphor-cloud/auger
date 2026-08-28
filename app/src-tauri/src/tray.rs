//! Menu bar presence.
//!
//! The rig runs all day, so the tray is the primary surface. The window is secondary and
//! stays hidden until the user asks for it.

use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::{TrayIcon, TrayIconBuilder};
use tauri::{AppHandle, Manager, Runtime};

pub const OPEN: &str = "open";
pub const QUIT: &str = "quit";
pub const TRAY_ID: &str = "main";

pub fn build<R: Runtime>(app: &AppHandle<R>, status: &str) -> tauri::Result<()> {
    let status_item = MenuItem::with_id(app, "status", status, false, None::<&str>)?;
    let open_item = MenuItem::with_id(app, OPEN, "Open auger", true, None::<&str>)?;
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

    TrayIconBuilder::with_id(TRAY_ID)
        .icon(app.default_window_icon().cloned().expect("bundled icon"))
        .icon_as_template(true)
        .tooltip("auger")
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

/// Show the open finding count beside the icon.
///
/// The rig runs all day with its window closed, so the menu bar is where the user learns
/// that something needs them. A count of zero shows no text, to keep the bar quiet.
pub fn set_status<R: Runtime>(app: &AppHandle<R>, open: u32, loud: u32) {
    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return;
    };
    let title = match (open, loud) {
        (0, _) => String::new(),
        (open, 0) => format!("{open}"),
        (open, loud) => format!("{open} ({loud})"),
    };
    apply(&tray, &title, open, loud);
}

fn apply<R: Runtime>(tray: &TrayIcon<R>, title: &str, open: u32, loud: u32) {
    let _ = tray.set_title(Some(title));
    let _ = tray.set_tooltip(Some(&match open {
        0 => "auger: nothing open".to_string(),
        _ => format!("auger: {open} open, {loud} that need attention"),
    }));
}
