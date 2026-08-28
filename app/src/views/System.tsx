import type { System } from "../types";

export default function SystemView({ system }: { system: System | null }) {
  if (system === null) return <p className="muted">Loading</p>;

  const { sandbox, egress } = system;
  return (
    <section>
      <header className="view-header">
        <div>
          <h2>System</h2>
          <p className="muted">Engine {system.version}</p>
        </div>
      </header>

      <h3>Sandbox</h3>
      <dl className="facts">
        <dt>Backend</dt>
        <dd>{sandbox.backend}</dd>
        <dt>Analysis image</dt>
        <dd className="mono">{system.image}</dd>
        <dt>Network</dt>
        <dd>None. A review step cannot reach anything.</dd>
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
