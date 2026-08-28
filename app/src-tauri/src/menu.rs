//! The application menu.
//!
//! An accessory application has no menu bar of its own, and without one macOS gives the
//! window no Quit, no Close, and no Cut, Copy, or Paste. A text field in Settings then
//! refuses ⌘V, which reads as a broken window rather than a deliberate one.
//!
//! So the window gets a real menu, and the dock icon appears while it is open and goes
//! away when it is closed. The menu bar stays the way to reach the rig when no window is
//! showing.

use tauri::menu::{Menu, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, Runtime};

/// Build the menu that gives the window its standard behaviour.
///
/// # Errors
///
/// Returns the Tauri error when a menu item cannot be built.
pub fn build<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<Menu<R>> {
    let application = Submenu::with_items(
        app,
        "Auger",
        true,
        &[
            &PredefinedMenuItem::about(app, None, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::hide(app, None)?,
            &PredefinedMenuItem::hide_others(app, None)?,
            &PredefinedMenuItem::show_all(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            // Quit stops the whole application, including the engine. The window's own
            // close button only hides it, which is what a background rig needs.
            &PredefinedMenuItem::quit(app, Some("Quit Auger"))?,
        ],
    )?;

    let edit = Submenu::with_items(
        app,
        "Edit",
        true,
        &[
            &PredefinedMenuItem::undo(app, None)?,
            &PredefinedMenuItem::redo(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, None)?,
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
        ],
    )?;

    let window = Submenu::with_items(
        app,
        "Window",
        true,
        &[
            &PredefinedMenuItem::minimize(app, None)?,
            // Close hides the window. The rig keeps working, and the menu bar icon
            // brings it back.
            &PredefinedMenuItem::close_window(app, Some("Close Window"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::fullscreen(app, None)?,
        ],
    )?;

    Menu::with_items(app, &[&application, &edit, &window])
}
