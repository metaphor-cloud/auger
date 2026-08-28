import { Alert, AlertDescription, Badge, Switch } from "@metaphor-cloud/ui";
import { useEffect, useState } from "react";

import { getAutostart, setAutostart } from "../host";
import type { System } from "../types";
import { Fact, Facts, Mono, PageTitle, Section } from "../ui";

export default function SystemView({
  system,
  nested = false,
}: {
  system: System | null;
  nested?: boolean;
}) {
  const [startsAtLogin, setStartsAtLogin] = useState(false);

  useEffect(() => {
    void getAutostart().then(setStartsAtLogin);
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
