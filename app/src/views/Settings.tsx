import { useCallback, useEffect, useState } from "react";

import {
  changeExclusion,
  changeSettings,
  checkTools,
  getForges,
  getSettings,
  getTools,
  setCodegraph,
} from "../engine";
import type { Forge, McpServer, Mode, PolicyLevel, Settings } from "../types";

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
  const [servers, setServers] = useState<McpServer[]>([]);
  const [allowed, setAllowed] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pattern, setPattern] = useState("");
  const [instructions, setInstructions] = useState("");

  const load = useCallback(async () => {
    try {
      const body = await getSettings();
      setSettings(body);
      setInstructions(body.defaults.instructions);
      setForges((await getForges()).forges);
      const tools = await getTools();
      setServers(tools.servers);
      setAllowed(tools.allowed);
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

      <h3>What to look for</h3>
      <p className="muted">
        Your instructions to the reviewer. They can narrow what it reports, add something
        to look for, or change how it judges severity. A repository&apos;s own{" "}
        <code>hints</code> are separate, and are treated as data.
      </p>
      <textarea
        rows={5}
        value={instructions}
        placeholder="Report security defects and data loss. Ignore performance."
        onChange={(event) => setInstructions(event.target.value)}
        onBlur={() => {
          if (instructions !== settings.defaults.instructions) {
            void change("defaults", "", { instructions });
          }
        }}
      />

      <h3>Excluded repositories</h3>
      <p className="muted">
        A path, a glob, or a forge key such as <code>github.com/acme</code>. An excluded
        repository stays listed and is never reviewed.
      </p>
      {settings.exclude.length > 0 && (
        <ul className="chips">
          {settings.exclude.map((entry) => (
            <li key={entry}>
              <span className="mono">{entry}</span>
              <button
                title="Stop excluding this"
                onClick={() =>
                  void changeExclusion(entry, true).then(setSettings).catch(() => undefined)
                }
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="setup-row">
        <input
          value={pattern}
          placeholder="~/git/scratch"
          onChange={(event) => setPattern(event.target.value)}
        />
        <button
          disabled={!pattern.trim()}
          onClick={() =>
            void changeExclusion(pattern, false)
              .then((body) => {
                setSettings(body);
                setPattern("");
              })
              .catch((cause) => setError(String(cause)))
          }
        >
          Exclude
        </button>
      </div>

      <h3>Call graph</h3>
      <dl className="facts">
        <dt>Use CodeGraph</dt>
        <dd>
          <input
            type="checkbox"
            checked={settings.codegraph}
            disabled={!settings.codegraph_available}
            onChange={(event) =>
              void setCodegraph(event.target.checked).then(setSettings)
            }
          />{" "}
          <span className="muted">
            {settings.codegraph_available
              ? "Asks a real call graph who calls a changed symbol, where a repository has an index."
              : "codegraph is not installed."}
          </span>
        </dd>
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

      <h3>Tools</h3>
      {servers.length === 0 ? (
        <p className="muted">
          No MCP server attached. Add an <code>[mcp.&quot;name&quot;]</code> section to the
          config file. A server runs outside the sandbox and speaks for you.
        </p>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>Server</th>
                <th>Runs</th>
                <th>State</th>
                <th>Tools</th>
              </tr>
            </thead>
            <tbody>
              {servers.map((server) => (
                <tr key={server.name}>
                  <td>{server.name}</td>
                  <td className="muted mono path" title={server.target}>
                    {server.target}
                  </td>
                  <td>
                    <span className={`badge ${server.reachable ? "mode-complete" : ""}`}>
                      {server.reachable ? "ready" : "unavailable"}
                    </span>
                  </td>
                  <td className="muted mono" title={server.reason ?? ""}>
                    {server.reachable
                      ? server.tools.map((tool) => tool.name).join(", ")
                      : server.reason}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">
            Allowed by default: {allowed.length ? allowed.join(", ") : "nothing"}. A tool
            runs only when a policy names it, as <code>server.tool</code> or{" "}
            <code>server.*</code>.
          </p>
          <button onClick={() => void checkTools().then((body) => setServers(body.servers))}>
            Check servers
          </button>
        </>
      )}
    </section>
  );
}
