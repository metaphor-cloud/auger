//! Supervision of the Python engine sidecar.
//!
//! The host owns the secret. It generates a token, passes it in the environment, and
//! hands it to the web UI over IPC. The token never appears in the process arguments,
//! where any local user could read it with `ps`.
//!
//! The engine binds port 0 and prints the port it got as its first log line. The host
//! reads that line instead of guessing a port that another process may already hold.
//!
//! A killed host cannot clean up, so the engine watches its own parent process id and
//! stops when that changes.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, ChildStderr, ChildStdout, Command, Stdio};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine as _;
use rand::RngCore;
use serde::Serialize;
use tauri::{AppHandle, Manager};

/// Where the bundle keeps the engine, relative to the resource directory.
const BUNDLED_ENGINE: &str = "engine/auger";

/// Set this to run a different engine, for example `uv run auger` in a
/// development checkout. The value is a command line, split on whitespace.
const ENGINE_COMMAND_OVERRIDE: &str = "AUGER_ENGINE_CMD";

const START_TIMEOUT: Duration = Duration::from_secs(30);

/// How long the engine gets to stop its model servers before it is ended.
const STOP_TIMEOUT: Duration = Duration::from_secs(12);

#[derive(Debug, Clone, Serialize)]
pub struct EngineInfo {
    pub port: u16,
    pub token: String,
}

#[derive(Debug)]
pub enum StartError {
    NoEngine(PathBuf),
    Spawn(std::io::Error),
    NoPort,
    Timeout,
}

impl std::fmt::Display for StartError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NoEngine(path) => write!(
                f,
                "engine not found at {}. Set {ENGINE_COMMAND_OVERRIDE} to run it from a checkout",
                path.display()
            ),
            Self::Spawn(error) => write!(f, "could not start the engine: {error}"),
            Self::NoPort => write!(f, "the engine exited before it reported a port"),
            Self::Timeout => write!(
                f,
                "the engine did not report a port within {START_TIMEOUT:?}"
            ),
        }
    }
}

/// A running engine. Dropping this kills the child and closes its stdin.
pub struct Engine {
    child: Child,
    info: EngineInfo,
}

impl Engine {
    pub fn info(&self) -> &EngineInfo {
        &self.info
    }

    /// Ask the engine to stop, and insist if it will not.
    ///
    /// A killed engine never runs its own shutdown, so the model servers it started
    /// keep tens of gigabytes for as long as the machine is up. The polite signal is
    /// what gives it the chance to stop them.
    pub fn stop(mut self) {
        #[cfg(unix)]
        {
            // A child pid always fits a pid_t, because that is where it came from.
            let pid = libc::pid_t::try_from(self.child.id()).unwrap_or(0);
            // SAFETY: `kill` with a pid this process owns and a valid signal number.
            unsafe {
                libc::kill(pid, libc::SIGTERM);
            }
            let deadline = std::time::Instant::now() + STOP_TIMEOUT;
            while std::time::Instant::now() < deadline {
                match self.child.try_wait() {
                    Ok(Some(_)) => return,
                    Ok(None) => thread::sleep(Duration::from_millis(100)),
                    Err(_) => break,
                }
            }
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// Managed application state. `None` until the engine reports its port.
#[derive(Default)]
pub struct EngineState(pub Mutex<Option<Engine>>);

fn generate_token() -> String {
    let mut bytes = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut bytes);
    URL_SAFE_NO_PAD.encode(bytes)
}

fn engine_command(app: &AppHandle) -> Result<Command, StartError> {
    if let Ok(override_line) = std::env::var(ENGINE_COMMAND_OVERRIDE) {
        let mut parts = override_line.split_whitespace();
        let program = parts.next().unwrap_or_default();
        let mut command = Command::new(program);
        command.args(parts);
        return Ok(command);
    }
    let path = app
        .path()
        .resource_dir()
        .map_err(|_| StartError::NoEngine(PathBuf::from(BUNDLED_ENGINE)))?
        .join(BUNDLED_ENGINE);
    if !path.exists() {
        return Err(StartError::NoEngine(path));
    }
    Ok(Command::new(path))
}

/// Forward one engine log line to the host console, and report the port when it appears.
fn watch_stdout(stdout: ChildStdout, port_tx: mpsc::Sender<u16>) {
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(port) = parse_port(&line) {
                let _ = port_tx.send(port);
            }
            println!("{line}");
        }
    });
}

fn watch_stderr(stderr: ChildStderr) {
    thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            eprintln!("{line}");
        }
    });
}

/// The engine logs one JSON object per line. Read the port out of the listening line.
fn parse_port(line: &str) -> Option<u16> {
    let value: serde_json::Value = serde_json::from_str(line).ok()?;
    if value.get("message")?.as_str()? != "engine listening" {
        return None;
    }
    u16::try_from(value.get("data")?.get("port")?.as_u64()?).ok()
}

pub fn start(app: &AppHandle) -> Result<Engine, StartError> {
    let token = generate_token();
    let mut command = engine_command(app)?;
    command
        .env("AUGER_TOKEN", &token)
        .env("AUGER_PORT", "0")
        .env("AUGER_HOST", "127.0.0.1")
        // The engine reads nothing. It notices a dead host by its parent process id.
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = command.spawn().map_err(StartError::Spawn)?;
    let (port_tx, port_rx) = mpsc::channel();
    if let Some(stdout) = child.stdout.take() {
        watch_stdout(stdout, port_tx);
    }
    if let Some(stderr) = child.stderr.take() {
        watch_stderr(stderr);
    }

    match port_rx.recv_timeout(START_TIMEOUT) {
        Ok(port) => Ok(Engine {
            child,
            info: EngineInfo { port, token },
        }),
        Err(reason) => {
            let _ = child.kill();
            let _ = child.wait();
            Err(match reason {
                RecvTimeoutError::Timeout => StartError::Timeout,
                RecvTimeoutError::Disconnected => StartError::NoPort,
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::parse_port;

    #[test]
    fn reads_the_port_from_the_listening_line() {
        let line = r#"{"level":"info","message":"engine listening","data":{"port":53123}}"#;
        assert_eq!(parse_port(line), Some(53123));
    }

    #[test]
    fn ignores_any_other_line() {
        assert_eq!(
            parse_port(r#"{"message":"repo scanned","data":{"port":1}}"#),
            None
        );
        assert_eq!(parse_port("not json"), None);
        assert_eq!(parse_port(r#"{"message":"engine listening"}"#), None);
    }

    #[test]
    fn rejects_a_port_outside_the_range() {
        assert_eq!(
            parse_port(r#"{"message":"engine listening","data":{"port":70000}}"#),
            None
        );
    }
}
