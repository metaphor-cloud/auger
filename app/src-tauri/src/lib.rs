//! The reviewrig host.
//!
//! Rust owns the tray, the window, the engine lifecycle, and the token. It holds no
//! review logic. Every decision about repositories, policy, and models lives in the
//! Python engine.

mod engine;
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

/// Start the host. This call returns when the user quits.
///
/// # Panics
///
/// Panics if the Tauri context fails to build, or if the engine state lock is poisoned.
/// Both mean the process cannot serve its purpose, so there is nothing to recover to.
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(EngineState::default())
        .invoke_handler(tauri::generate_handler![engine_info])
        .setup(|app| {
            // The menu bar is the product. Keep the dock clear.
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);

            let handle = app.handle().clone();
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
            // Closing the window must not quit a background application. Hide it.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
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
