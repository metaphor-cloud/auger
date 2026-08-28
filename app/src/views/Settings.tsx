import { useCallback, useEffect, useState } from "react";

import { changeSettings, getForges, getSettings } from "../engine";
import type { Forge, Mode, PolicyLevel, Settings } from "../types";

const MODES: Mode[] = ["off", "draft", "complete"];

function ModeSelect({
  value,
  onChange,
}: {
  value: Mode | undefined;
  onChange: (mode: Mode) => void;
}) {
  return (
    <select value={value ?? ""} onChange={(event) => onChange(event.target.value as Mode)}>
      {value === undefined && <option value="">inherit</option>}
      {MODES.map((mode) => (
        <option key={mode} value={mode}>
          {mode}
        </option>
      ))}
    </select>
  );
}

export default function SettingsView() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [forges, setForges] = useState<Forge[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSettings(await getSettings());
      setForges((await getForges()).forges);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function change(
    level: "defaults" | "org" | "repo",
    key: string,
    changes: Record<string, unknown>,
  ) {
    try {
      setSettings(await changeSettings(level, key, changes));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  if (settings === null) return <p className="muted">{error ?? "Loading"}</p>;

  const complete = settings.levels.filter((level) => level.overrides.mode === "complete");
  const defaultsComplete = settings.defaults.mode === "complete";

  return (
    <section>
      <header className="view-header">
        <div>
          <h2>Settings</h2>
          <p className="muted mono">{settings.config_path}</p>
        </div>
      </header>

      {error && <p className="error">{error}</p>}
      {(complete.length > 0 || defaultsComplete) && (
        <p className="banner">
          Complete mode submits comments on real pull requests under your name:{" "}
          {defaultsComplete ? "every repository" : complete.map((one) => one.key).join(", ")}.
        </p>
      )}

      <h3>All repositories</h3>
      <dl className="facts">
        <dt>Mode</dt>
        <dd>
          <ModeSelect
            value={settings.defaults.mode}
            onChange={(mode) => void change("defaults", "", { mode })}
          />
        </dd>
        <dt>Review pull requests assigned to me</dt>
        <dd>
          <input
            type="checkbox"
            checked={settings.defaults.auto_review_assigned_prs}
            onChange={(event) =>
              void change("defaults", "", { auto_review_assigned_prs: event.target.checked })
            }
          />
        </dd>
        <dt>Priority</dt>
        <dd className="mono">{settings.defaults.priority}</dd>
        <dt>Wait after an agent stops</dt>
        <dd className="mono">{settings.defaults.idle_seconds}s</dd>
      </dl>

      <h3>Overrides</h3>
      {settings.levels.length === 0 ? (
        <p className="muted">
          None. Add an <code>[org.&quot;host/name&quot;]</code> or{" "}
          <code>[repo.&quot;/path&quot;]</code> section to the config file.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Level</th>
              <th>Applies to</th>
              <th>Mode</th>
              <th>Assigned only</th>
              <th>Other</th>
            </tr>
          </thead>
          <tbody>
            {settings.levels.map((level: PolicyLevel) => (
              <tr key={`${level.level}:${level.key}`}>
                <td>{level.level}</td>
                <td className="mono path" title={level.key}>
                  {level.key}
                </td>
                <td>
                  <ModeSelect
                    value={level.overrides.mode as Mode | undefined}
                    onChange={(mode) => void change(level.level, level.key, { mode })}
                  />
                </td>
                <td>
                  <input
                    type="checkbox"
                    checked={level.overrides.auto_review_assigned_prs !== false}
                    onChange={(event) =>
                      void change(level.level, level.key, {
                        auto_review_assigned_prs: event.target.checked,
                      })
                    }
                  />
                </td>
                <td className="muted mono">
                  {Object.keys(level.overrides)
                    .filter((name) => name !== "mode" && name !== "auto_review_assigned_prs")
                    .join(", ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>Forges</h3>
      <table>
        <thead>
          <tr>
            <th>Forge</th>
            <th>Host</th>
            <th>State</th>
            <th>Signed in as</th>
          </tr>
        </thead>
        <tbody>
          {forges.map((forge) => (
            <tr key={forge.name} className={forge.enabled ? "" : "disabled"}>
              <td>{forge.name}</td>
              <td className="muted mono">{forge.host}</td>
              <td>
                <span className={`badge ${forge.reachable ? "mode-complete" : ""}`}>
                  {!forge.enabled ? "off" : forge.reachable ? "connected" : "unavailable"}
                </span>
              </td>
              <td className="muted" title={forge.reason ?? ""}>
                {forge.user || forge.reason || ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">
        Turn a forge on in the config file. The rig then reads its pull requests and adds it
        to the egress allowlist.
      </p>
    </section>
  );
}
