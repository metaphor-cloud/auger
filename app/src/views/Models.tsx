import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  Progress,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@metaphor-cloud/ui";
import { useCallback, useEffect, useState } from "react";

import {
  checkModels,
  getCatalog,
  getModels,
  setupModels,
  startModels,
  stopModels,
} from "../engine";
import type { BackendList, Catalog, ModelChoice, SetupProgress } from "../types";
import ModelSearch from "../parts/ModelSearch";
import { Fact, Facts, Mono, PageTitle, Section } from "../ui";

const JOB_CLASSES = ["review", "verify", "triage", "embed", "rerank"];

function label(model: ModelChoice) {
  const notes = [`${model.memory_gb.toFixed(0)} GB`];
  if (model.downloaded) notes.push("downloaded");
  if (!model.fits) notes.push("too large for this machine");
  if (model.gated && !model.from_publisher) notes.push("a community build");
  return `${model.name} · ${notes.join(" · ")}`;
}

function Picker({
  value,
  onChange,
  models,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  models: ModelChoice[];
  placeholder: string;
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-full max-w-xl">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {models.map((model) => (
          <SelectItem key={model.name} value={model.name} disabled={!model.fits}>
            {label(model)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export default function Models({
  setup,
  nested = false,
}: {
  setup: SetupProgress | null;
  nested?: boolean;
}) {
  const [data, setData] = useState<BackendList | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [review, setReview] = useState("");
  const [embed, setEmbed] = useState("");
  const [adversary, setAdversary] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"" | "check" | "start" | "stop" | "setup">("");

  const load = useCallback(async (fetcher: () => Promise<BackendList>) => {
    try {
      setData(await fetcher());
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  const loadCatalog = useCallback(async () => {
    const body = await getCatalog();
    setCatalog(body);
    setReview((current) => current || body.recommended);
    setEmbed(
      (current) =>
        current || body.models.find((one) => one.job_class === "embed" && one.fits)?.name || "",
    );
  }, []);

  useEffect(() => {
    void load(getModels);
    void loadCatalog();
  }, [load, loadCatalog]);

  const starting = (data?.backends ?? []).some((one) => one.state === "starting");

  useEffect(() => {
    // A large model takes minutes to load. Without this the table says the same thing
    // for those minutes, and the button reads as one that did nothing.
    if (!starting) return;
    const timer = setInterval(() => void load(checkModels), 3000);
    return () => clearInterval(timer);
  }, [starting, load]);

  async function act(which: "check" | "start" | "stop") {
    setBusy(which);
    const call =
      which === "check" ? checkModels : which === "start" ? startModels : () => stopModels();
    await load(call);
    setBusy("");
  }

  async function stopOne(name: string) {
    setBusy("stop");
    await load(() => stopModels(name));
    setBusy("");
  }

  async function fetchModels() {
    setBusy("setup");
    try {
      const outcome = await setupModels(review, embed, adversary);
      if (!outcome.ok) setError(outcome.error);
      await load(getModels);
      await loadCatalog();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  const reviewers = catalog?.models.filter((one) => one.job_class === "review") ?? [];
  const embedders = catalog?.models.filter((one) => one.job_class === "embed") ?? [];
  const adversaries = catalog?.models.filter((one) => one.job_class === "verify") ?? [];
  const chosen = [review, embed, adversary]
    .map((name) => catalog?.models.find((one) => one.name === name))
    .filter((one): one is ModelChoice => Boolean(one));
  const toFetch = chosen.filter((one) => !one.downloaded);

  return (
    <>
      <PageTitle
        title={nested ? "" : "Models"}
        description={nested ? "" : "A job asks for a job class. The profile picks the backend."}
      >
        <Button size="sm" variant="ghost" onClick={() => act("check")} disabled={busy !== ""}>
          {busy === "check" ? "Checking" : "Check"}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          title="Start every managed server that does not answer"
          onClick={() => act("start")}
          disabled={busy !== "" || starting}
        >
          {busy === "start" || starting ? "Loading" : "Start"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          title="Stop every server the rig started, and give the memory back"
          onClick={() => act("stop")}
          disabled={busy !== "" || !(data?.backends ?? []).some((one) => one.ours)}
        >
          {busy === "stop" ? "Stopping" : "Unload all"}
        </Button>
      </PageTitle>

      {error && (
        <Alert variant="danger" className="mb-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <Section
        title="Choose and fetch"
        description={`The rig brings its own runtime and its own weights. This machine can hold about ${
          catalog?.usable_memory_gb ?? "?"
        } GB.`}
      >
        <div className="space-y-3">
          <div>
            <p className="mb-1 text-xs text-text-secondary">Reviewer</p>
            <Picker
              value={review}
              onChange={setReview}
              models={reviewers}
              placeholder="Pick a review model"
            />
          </div>
          <div>
            <p className="mb-1 text-xs text-text-secondary">Embedder</p>
            <Picker
              value={embed}
              onChange={setEmbed}
              models={embedders}
              placeholder="Pick an embedding model"
            />
          </div>
          {adversaries.length > 0 && (
            <div>
              <p className="mb-1 text-xs text-text-secondary">
                Second opinion, from another family
              </p>
              <Picker
                value={adversary}
                onChange={setAdversary}
                models={adversaries}
                placeholder="None. The reviewer judges its own work."
              />
            </div>
          )}
          <p className="text-xs text-text-secondary">
            {chosen.map((one) => `${one.name} (${one.memory_gb.toFixed(0)} GB to run)`).join(", ") ||
              "Nothing chosen"}
            {toFetch.length > 0 && ` · ${toFetch.length} to download`}
          </p>
          {chosen.some((one) => one.gated && !one.from_publisher) && (
            <p className="text-[11px] text-text-tertiary">
              {chosen
                .filter((one) => one.gated && !one.from_publisher)
                .map((one) => `${one.name} comes from ${one.from_repo}`)
                .join(", ")}
              . Its publisher wants a licence accepted in a browser first. Accept it and
              set a Hugging Face token, and Auger fetches their own file instead.
            </p>
          )}
          <div className="flex items-center gap-3">
            <Button onClick={() => void fetchModels()} disabled={busy !== "" || !review}>
              {busy === "setup"
                ? "Working"
                : toFetch.length === 0
                  ? "Use these"
                  : `Download and use (${toFetch.length})`}
            </Button>
            {catalog && !catalog.runtime_installed && (
              <span className="text-xs text-text-secondary">
                The model runtime is fetched too, once.
              </span>
            )}
          </div>
          {setup && (
            <div className="space-y-1">
              <Mono>
                {setup.total
                  ? `${setup.name} ${(setup.fraction * 100).toFixed(1)}% (${(
                      setup.received / 1e9
                    ).toFixed(1)} GB)`
                  : setup.message}
              </Mono>
              {setup.total > 0 && <Progress value={setup.fraction * 100} />}
            </div>
          )}
        </div>
      </Section>

      <Section
        title="Find a model"
        description="The list above is what Auger recommends. This is everything else Hugging Face publishes as one loadable file."
      >
        <ModelSearch
          onFetched={() => {
            void load(getModels);
            void loadCatalog();
          }}
        />
      </Section>

      <Section
        title="Backends"
        description={
          starting
            ? "A server is loading its weights. A large model takes a few minutes, and this follows it."
            : "What answers a request, and what it has cost so far. Unload gives the memory back, and the next review starts it again."
        }
      >
        {data === null ? (
          <p className="text-xs text-text-secondary">Loading</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Backend</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>State</TableHead>
                <TableHead>Address</TableHead>
                <TableHead className="text-right">Requests</TableHead>
                <TableHead className="text-right">Tokens</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.backends.map((backend) => (
                <TableRow key={backend.name}>
                  <TableCell>
                    {backend.name}
                    {backend.hosted && (
                      <Badge variant="warning" className="ml-2">
                        hosted
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-text-secondary">
                    {backend.model || "any"}
                    {!backend.downloaded && (
                      <span className="ml-1.5 text-[10px] text-warning">
                        not on this machine
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={
                        backend.state === "running"
                          ? "success"
                          : backend.state === "starting"
                            ? "warning"
                            : "outline"
                      }
                    >
                      {backend.state === "running"
                        ? "Running"
                        : backend.state === "starting"
                          ? "Loading"
                          : backend.downloaded
                            ? "Stopped"
                            : "No weights"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Mono title={backend.reason ?? backend.url}>{backend.url}</Mono>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{backend.requests}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {backend.prompt_tokens + backend.completion_tokens}
                  </TableCell>
                  <TableCell className="text-right">
                    {backend.ours && (
                      <Button
                        size="sm"
                        variant="ghost"
                        title="Stop this server and give its memory back"
                        onClick={() => void stopOne(backend.name)}
                        disabled={busy !== ""}
                      >
                        Unload
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Section>

      {data !== null && (
        <Section
          title="Active profile"
          description={
            data.allow_hosted
              ? "Hosted models are allowed."
              : "Hosted models are off. Turning one on sends your code off this machine."
          }
        >
          <Facts>
            {JOB_CLASSES.map((jobClass) => (
              <Fact key={jobClass} label={jobClass}>
                <Mono>{data.active_profile_backends[jobClass] || "off"}</Mono>
              </Fact>
            ))}
          </Facts>
        </Section>
      )}
    </>
  );
}
