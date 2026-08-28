//! The auger host.
//!
//! Rust owns the tray, the window, the engine lifecycle, and the token. It holds no
//! review logic. Every decision about repositories, policy, and models lives in the
//! Python engine.

mod engine;
mod menu;
mod tray;

use engine::{EngineInfo, EngineState};
use tauri::{Manager, RunEvent, WindowEvent};

/// The web UI needs the port and the token to reach the engine. IPC is the only channel
/// that carries them, so they never appear in a URL or in a log.
#[tauri::command]
// Tauri fixes this signature. A command takes its state by value.
#[allow(clippy::needless_pass_by_value)]
fn engine_info(state: tauri::State<'_, EngineState>) -> Result<EngineInfo, String> {
    state
        .0
        .lock()
        .map_err(|_| "engine state is poisoned".to_string())?
        .as_ref()
        .map(|running| running.info().clone())
        .ok_or_else(|| "the engine is not running".to_string())
}

/// The UI holds the event stream, so it tells the host what the tray should say.
#[tauri::command]
// Tauri fixes this signature. A command takes its handle by value.
#[allow(clippy::needless_pass_by_value)]
fn set_tray_status(app: tauri::AppHandle, open: u32, critical: u32) {
    tray::set_status(&app, open, critical);
}

/// A finding that needs attention reaches the user even with the window closed.
#[tauri::command]
#[allow(clippy::needless_pass_by_value)]
fn notify(app: tauri::AppHandle, title: String, body: String) -> Result<(), String> {
    use tauri_plugin_notification::NotificationExt;
    app.notification()
        .builder()
        .title(title)
        .body(body)
        .show()
        .map_err(|error| error.to_string())
}

/// Whether the rig starts with the machine.
///
/// The rig is a background application: it is useful only while it runs. Starting at
/// login is therefore the normal setting, and it stays the user's to turn off.
#[tauri::command]
#[allow(clippy::needless_pass_by_value)]
fn autostart(app: tauri::AppHandle) -> Result<bool, String> {
    use tauri_plugin_autostart::ManagerExt;
    app.autolaunch()
        .is_enabled()
        .map_err(|error| error.to_string())
}

#[tauri::command]
#[allow(clippy::needless_pass_by_value)]
fn set_autostart(app: tauri::AppHandle, enabled: bool) -> Result<bool, String> {
    use tauri_plugin_autostart::ManagerExt;
    let launcher = app.autolaunch();
    let result = if enabled {
        launcher.enable()
    } else {
        launcher.disable()
    };
    result.map_err(|error| error.to_string())?;
    launcher.is_enabled().map_err(|error| error.to_string())
}

/// Start the host. This call returns when the user quits.
///
/// # Panics
///
/// Panics if the Tauri context fails to build, or if the engine state lock is poisoned.
/// Both mean the process cannot serve its purpose, so there is nothing to recover to.
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .manage(EngineState::default())
        .invoke_handler(tauri::generate_handler![
            engine_info,
            set_tray_status,
            notify,
            autostart,
            set_autostart
        ])
        .setup(|app| {
            // With no window open the rig is a menu bar application, so the dock stays
            // clear. Opening the window turns it into a normal one, which is what gives
            // it a menu bar, a dock icon, and ⌘Q.
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);

            let handle = app.handle().clone();
            app.set_menu(menu::build(&handle)?)?;
            let status = match engine::start(&handle) {
                Ok(running) => {
                    let status = format!("Engine on port {}", running.info().port);
                    *app.state::<EngineState>().0.lock().unwrap() = Some(running);
                    status
                }
                Err(error) => {
                    // A failure here leaves the tray with no engine. Say so in the menu
                    // rather than exiting, so the user can read the reason.
                    eprintln!(
                        "{}",
                        serde_json::json!({
                            "level": "error",
                            "message": "engine start failed",
                            "component": "host",
                            "data": {"reason": error.to_string()},
                        })
                    );
                    format!("Engine stopped: {error}")
                }
            };
            tray::build(&handle, &status)?;
            // A development run shows the window at once. A packaged run starts in the
            // menu bar and waits for the user to ask for it.
            #[cfg(debug_assertions)]
            tray::show_window(&handle);
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window must not quit a background application. Hide it, and
            // give the dock icon back, so the rig is a menu bar application again.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
                #[cfg(target_os = "macos")]
                let _ = window
                    .app_handle()
                    .set_activation_policy(tauri::ActivationPolicy::Accessory);
            }
        })
        .build(tauri::generate_context!())
        .expect("error while running tauri application")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(running) = app.state::<EngineState>().0.lock().unwrap().take() {
                    running.stop();
                }
            }
        });
}
