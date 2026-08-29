import { Alert, AlertDescription, Badge, Button, Switch } from "@metaphor-cloud/ui";
import { getVersion } from "@tauri-apps/api/app";
import { useEffect, useState } from "react";

import {
  checkForUpdate,
  getAutostart,
  installUpdate,
  setAutostart,
  type UpdateState,
} from "../host";
import type { System } from "../types";
import { Fact, Facts, Mono, PageTitle, Section } from "../ui";

/** What the update line says for each state the updater can be in. */
function updateMessage(state: UpdateState): string {
  switch (state.kind) {
    case "checking":
      return "Asking GitHub.";
    case "current":
      return "This is the newest release.";
    case "available":
      return `Version ${state.version} is ready to install.`;
    case "installing":
      return "Downloading. This takes a moment.";
    case "ready":
      return "Installed. Quit and open Auger again to run it.";
    case "failed":
      return state.reason;
    default:
      return "";
  }
}

export default function SystemView({
  system,
  nested = false,
}: {
  system: System | null;
  nested?: boolean;
}) {
  const [startsAtLogin, setStartsAtLogin] = useState(false);
  const [appVersion, setAppVersion] = useState("");
  const [update, setUpdate] = useState<UpdateState>({ kind: "idle" });

  useEffect(() => {
    void getAutostart().then(setStartsAtLogin);
    void getVersion().then(setAppVersion, () => undefined);
  }, []);

  if (system === null) return <p className="text-xs text-text-secondary">Loading</p>;

  const { sandbox, egress, index } = system;
  return (
    <>
      {!nested && <PageTitle title="System" description={`Engine ${system.version}`} />}

      {sandbox.degraded && sandbox.warning && (
        <Alert variant="warning" className="mb-4">
          <AlertDescription>{sandbox.warning}</AlertDescription>
        </Alert>
      )}

      <Section title="Application">
        <Facts>
          <Fact label="Start at login">
            <div className="flex items-center gap-2">
              <Switch
                checked={startsAtLogin}
                onCheckedChange={(next) => void setAutostart(next).then(setStartsAtLogin)}
              />
              <span className="text-text-secondary">The rig is useful only while it runs.</span>
            </div>
          </Fact>
          <Fact label="Version">
            <div className="flex items-center gap-2">
              <Mono>{appVersion || "unknown"}</Mono>
              {update.kind === "available" ? (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    setUpdate({ kind: "installing" });
                    void installUpdate().then(setUpdate);
                  }}
                >
                  Install {update.version}
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={update.kind === "checking" || update.kind === "installing"}
                  onClick={() => {
                    setUpdate({ kind: "checking" });
                    void checkForUpdate().then(setUpdate);
                  }}
                >
                  Check for updates
                </Button>
              )}
              <span className="text-text-secondary">{updateMessage(update)}</span>
            </div>
          </Fact>
        </Facts>
      </Section>

      <Section title="Sandbox" description="Every analysis step runs here.">
        <Facts>
          <Fact label="Backend">
            <Badge variant={sandbox.degraded ? "warning" : "success"}>{sandbox.backend}</Badge>
          </Fact>
          <Fact label="Analysis image">
            <Mono>{system.image}</Mono>
          </Fact>
          <Fact label="Network">None. A review step cannot reach anything.</Fact>
        </Facts>
      </Section>

      <Section title="Code index" description="What retrieval can draw on.">
        <Facts>
          <Fact label="Indexed">
            {index.files} files, {index.chunks} chunks
          </Fact>
          <Fact label="Search by meaning">
            {index.vectors
              ? `${index.embedded} chunks embedded`
              : "unavailable, keyword search only"}
          </Fact>
        </Facts>
      </Section>

      <Section
        title="Egress"
        description="A review step has no network. This governs the engine and the tools it starts."
      >
        <Facts>
          <Fact label="Proxy">
            <Mono>{egress.proxy_url}</Mono>
          </Fact>
          <Fact label="Allowed">
            <Mono>{egress.allowed.length ? egress.allowed.join(", ") : "nothing yet"}</Mono>
          </Fact>
          <Fact label="Requests">
            {egress.allowed_requests} allowed, {egress.refused_requests} refused,{" "}
            {egress.failed_requests} failed
          </Fact>
          {egress.recently_refused.length > 0 && (
            <Fact label="Recently refused">
              <Mono>{egress.recently_refused.join(", ")}</Mono>
            </Fact>
          )}
        </Facts>
      </Section>
    </>
  );
}
