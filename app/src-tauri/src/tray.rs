//! Menu bar presence.
//!
//! The rig runs all day, so the tray is the primary surface. The window is secondary and
//! stays hidden until the user asks for it.

use tauri::image::Image;
use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::{TrayIcon, TrayIconBuilder};
use tauri::{AppHandle, Emitter, Manager, Runtime};

pub const OPEN: &str = "open";
pub const QUIT: &str = "quit";
pub const REVIEWING: &str = "reviewing";
pub const UNLOAD: &str = "unload";
pub const TRAY_ID: &str = "main";

/// The window carries the engine token and already talks to it, and it keeps running
/// while it is hidden, so the tray asks it to act rather than holding a second client.
pub const ACTION: &str = "tray://action";

/// The two items whose words change with the engine's state.
///
/// The rig runs whatever is queued, a review or a scan or an audit, so the words say
/// whether it is working rather than naming one kind of work.
///
/// A tray icon gives no way to read its own menu back, so the handles are kept here.
pub struct Actions<R: Runtime> {
    reviewing: MenuItem<R>,
    unload: MenuItem<R>,
}

/// The auger, as one black silhouette.
///
/// The menu bar draws a template from the alpha and colours it itself, so this is the
/// shape and nothing else. It is compiled in, which keeps it out of the bundle's
/// resource paths and out of reach of a missing file at runtime.
fn mark() -> Image<'static> {
    Image::from_bytes(include_bytes!("../icons/tray.png")).expect("the tray mark is valid PNG")
}

pub fn build<R: Runtime>(app: &AppHandle<R>, status: &str) -> tauri::Result<()> {
    let status_item = MenuItem::with_id(app, "status", status, false, None::<&str>)?;
    let open_item = MenuItem::with_id(app, OPEN, "Open Auger", true, None::<&str>)?;
    // Both start disabled. The window turns them on once it knows what the engine is
    // doing, so the menu never offers an action that would do nothing.
    let reviewing_item = MenuItem::with_id(app, REVIEWING, "Start", false, None::<&str>)?;
    let unload_item = MenuItem::with_id(app, UNLOAD, "Unload models", false, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, QUIT, "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &status_item,
            &PredefinedMenuItem::separator(app)?,
            &reviewing_item,
            &unload_item,
            &PredefinedMenuItem::separator(app)?,
            &open_item,
            &PredefinedMenuItem::separator(app)?,
            &quit_item,
        ],
    )?;

    app.manage(Actions {
        reviewing: reviewing_item.clone(),
        unload: unload_item.clone(),
    });

    TrayIconBuilder::with_id(TRAY_ID)
        .icon(mark())
        .icon_as_template(true)
        .tooltip("Auger")
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
        id @ (REVIEWING | UNLOAD) => {
            let _ = app.emit(ACTION, id.to_string());
        }
        _ => {}
    }
}

/// What the two action items say, and whether they can be used.
///
/// The window knows the engine's state, so it tells the tray. A menu that guessed
/// would offer Pause to a rig that has not started.
pub fn set_actions<R: Runtime>(app: &AppHandle<R>, reviewing: bool, ready: bool, loaded: bool) {
    let Some(actions) = app.try_state::<Actions<R>>() else {
        return;
    };
    let _ = actions
        .reviewing
        .set_text(if reviewing { "Pause" } else { "Start" });
    let _ = actions.reviewing.set_enabled(ready);
    let _ = actions.unload.set_enabled(loaded);
}

pub fn show_window<R: Runtime>(app: &AppHandle<R>) {
    // A window with no dock icon has no menu bar either, so it has no Quit and no
    // Paste. Become a normal application while the window is up.
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
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
        0 => "Auger: nothing open".to_string(),
        _ => format!("Auger: {open} open, {loud} that need attention"),
    }));
}
