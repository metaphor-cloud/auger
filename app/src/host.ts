/** The Rust host. It owns the tray and the notifications; the UI tells it what to show. */

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

/** What a tray menu item asks the window to do. */
export type TrayAction = "reviewing" | "unload";

export async function setTray(open: number, critical: number): Promise<void> {
  try {
    await invoke("set_tray_status", { open, critical });
  } catch {
    // The tray is not worth failing the window over.
  }
}

/** What the tray's action items say, and whether they can be used. */
export async function setTrayActions(
  reviewing: boolean,
  ready: boolean,
  loaded: boolean,
): Promise<void> {
  try {
    await invoke("set_tray_actions", { reviewing, ready, loaded });
  } catch {
    // The tray is not worth failing the window over.
  }
}

/** Listen for a tray menu item. The window acts, because it holds the engine token. */
export async function onTrayAction(act: (action: TrayAction) => void): Promise<() => void> {
  try {
    return await listen<TrayAction>("tray://action", (event) => act(event.payload));
  } catch {
    return () => undefined;
  }
}

export async function notify(title: string, body: string): Promise<void> {
  try {
    await invoke("notify", { title, body });
  } catch {
    // A missed notification is not worth failing the window over.
  }
}

export async function getAutostart(): Promise<boolean> {
  try {
    return await invoke<boolean>("autostart");
  } catch {
    return false;
  }
}

export async function setAutostart(enabled: boolean): Promise<boolean> {
  return invoke<boolean>("set_autostart", { enabled });
}
