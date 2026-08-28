import { useEffect, useState } from "react";

import { getAutostart, setAutostart } from "../host";
import type { System } from "../types";

export default function SystemView({ system }: { system: System | null }) {
  const [startsAtLogin, setStartsAtLogin] = useState(false);

  useEffect(() => {
    void getAutostart().then(setStartsAtLogin);
  }, []);

  if (system === null) return <p className="muted">Loading</p>;

  const { sandbox, egress, index } = system;
  return (
    <section>
      <header className="view-header">
        <div>
          <h2>System</h2>
          <p className="muted">Engine {system.version}</p>
        </div>
      </header>

      <h3>Application</h3>
      <dl className="facts">
        <dt>Start at login</dt>
        <dd>
          <input
            type="checkbox"
            checked={startsAtLogin}
            onChange={(event) =>
              void setAutostart(event.target.checked).then(setStartsAtLogin)
            }
          />{" "}
          <span className="muted">The rig is useful only while it runs.</span>
        </dd>
      </dl>

      <h3>Sandbox</h3>
      <dl className="facts">
        <dt>Backend</dt>
        <dd>{sandbox.backend}</dd>
        <dt>Analysis image</dt>
        <dd className="mono">{system.image}</dd>
        <dt>Network</dt>
        <dd>None. A review step cannot reach anything.</dd>
      </dl>

      <h3>Code index</h3>
      <dl className="facts">
        <dt>Indexed</dt>
        <dd>
          {index.files} files, {index.chunks} chunks
        </dd>
        <dt>Search by meaning</dt>
        <dd>
          {index.vectors
            ? `${index.embedded} chunks embedded`
            : "unavailable, keyword search only"}
        </dd>
      </dl>

      <h3>Egress</h3>
      <p className="muted">
        A review step has no network. This governs the engine and the tools it starts.
      </p>
      <dl className="facts">
        <dt>Proxy</dt>
        <dd className="mono">{egress.proxy_url}</dd>
        <dt>Allowed</dt>
        <dd className="mono">{egress.allowed.length ? egress.allowed.join(", ") : "nothing yet"}</dd>
        <dt>Requests</dt>
        <dd>
          {egress.allowed_requests} allowed, {egress.refused_requests} refused,{" "}
          {egress.failed_requests} failed
        </dd>
        {egress.recently_refused.length > 0 && (
          <>
            <dt>Recently refused</dt>
            <dd className="mono">{egress.recently_refused.join(", ")}</dd>
          </>
        )}
      </dl>
    </section>
  );
}
