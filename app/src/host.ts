/** The Rust host. It owns the tray and the notifications; the UI tells it what to show. */

import { invoke } from "@tauri-apps/api/core";

export async function setTray(open: number, critical: number): Promise<void> {
  try {
    await invoke("set_tray_status", { open, critical });
  } catch {
    // The tray is not worth failing the window over.
  }
}

export async function notify(title: string, body: string): Promise<void> {
  try {
    await invoke("notify", { title, body });
  } catch {
    // A missed notification is not worth failing the window over.
  }
}
