/** The first run.
 *
 * Three things must be true before the rig can do anything: it must know where to look,
 * it must have a model, and it must have found something to review. This walks through
 * those three and gets out of the way. It never appears again once it is finished.
 */

import { Button, Input, Progress, Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@metaphor-cloud/ui";
import { useCallback, useEffect, useState } from "react";

import {
  finishOnboarding,
  getCatalog,
  getOnboarding,
  getSettings,
  rescan,
  setSetting,
  setupModels,
} from "../engine";
import type { Catalog, ModelChoice, Onboarding, SetupProgress } from "../types";
import { Mono } from "../ui";

type Step = { key: string; title: string; blurb: string };

const STEPS: Step[] = [
  {
    key: "roots",
    title: "Where should it look?",
    blurb:
      "Name a directory that holds your git checkouts. The rig walks it, and it stops at every repository it finds.",
  },
  {
    key: "model",
    title: "Which model should review?",
    blurb:
      "The rig brings its own runtime and its own weights. Nothing else has to be installed, and your code never leaves this machine.",
  },
  {
    key: "ready",
    title: "Ready",
    blurb: "It watches from here. A change to a repository starts a review when nothing else is working in it.",
  },
];

export default function OnboardingView({
  setup,
  onDone,
}: {
  setup: SetupProgress | null;
  onDone: () => void;
}) {
  const [step, setStep] = useState(0);
  const [state, setState] = useState<Onboarding | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [root, setRoot] = useState("~/git");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setState(await getOnboarding());
      const body = await getCatalog();
      setCatalog(body);
      setModel((current) => current || body.recommended);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function addRoot() {
    setBusy("roots");
    try {
      const settings = await getSettings();
      const roots = settings.roots.map((one) => ({ path: one.path, exclude: one.exclude }));
      await setSetting("roots", [...roots, { path: root.trim(), exclude: [] }]);
      const scanned = await rescan();
      setState((current) =>
        current ? { ...current, roots: roots.length + 1, repositories: scanned.repositories.length } : current,
      );
      setStep(1);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  async function fetchModel() {
    setBusy("model");
    try {
      const outcome = await setupModels(model, "");
      if (!outcome.ok) {
        setError(outcome.error);
        return;
      }
      setStep(2);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  async function done() {
    await finishOnboarding(true);
    onDone();
  }

  const reviewers = (catalog?.models ?? []).filter((one) => one.job_class === "review");
  const chosen: ModelChoice | undefined = reviewers.find((one) => one.name === model);

  return (
    <div className="grid h-full place-items-center bg-bg px-6">
      <div className="w-full max-w-xl">
        <div className="mb-8 flex items-center gap-3">
          {STEPS.map((one, index) => (
            <div key={one.key} className="flex flex-1 items-center gap-3">
              <span
                className="grid h-6 w-6 place-items-center rounded-full border text-[11px] transition-colors"
                style={{
                  borderColor: index <= step ? "var(--color-accent)" : "var(--color-border)",
                  color: index <= step ? "var(--color-accent)" : "var(--color-text-tertiary)",
                  background: index < step ? "var(--color-accent)" : "transparent",
                }}
              >
                {index < step ? "" : index + 1}
              </span>
              {index < STEPS.length - 1 && (
                <span
                  className="h-px flex-1"
                  style={{
                    background: index < step ? "var(--color-accent)" : "var(--color-border)",
                  }}
                />
              )}
            </div>
          ))}
        </div>

        <h1 className="text-xl font-semibold tracking-tight">{STEPS[step].title}</h1>
        <p className="mt-2 text-sm leading-relaxed text-text-secondary">{STEPS[step].blurb}</p>

        {error && <p className="mt-4 text-xs text-danger">{error}</p>}

        {step === 0 && (
          <div className="mt-6 flex gap-2">
            <Input value={root} onChange={(event) => setRoot(event.target.value)} />
            <Button disabled={!root.trim() || busy !== ""} onClick={() => void addRoot()}>
              {busy === "roots" ? "Walking" : "Use this"}
            </Button>
          </div>
        )}

        {step === 1 && (
          <div className="mt-6 space-y-3">
            <Select value={model} onValueChange={setModel}>
              <SelectTrigger>
                <SelectValue placeholder="Pick a model" />
              </SelectTrigger>
              <SelectContent>
                {reviewers.map((one) => (
                  <SelectItem key={one.name} value={one.name} disabled={!one.fits}>
                    {one.name} · {one.memory_gb.toFixed(0)} GB
                    {one.downloaded ? " · downloaded" : ""}
                    {one.fits ? "" : " · too large for this machine"}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-text-tertiary">
              This machine can hold about {catalog?.usable_memory_gb ?? "?"} GB.
              {chosen && !chosen.downloaded ? " The weights are fetched once." : ""}
            </p>
            {setup && (
              <div className="space-y-1">
                <Mono>
                  {setup.total
                    ? `${setup.name} ${(setup.fraction * 100).toFixed(1)}%`
                    : setup.message}
                </Mono>
                {setup.total > 0 && <Progress value={setup.fraction * 100} />}
              </div>
            )}
            <div className="flex gap-2">
              <Button disabled={!model || busy !== ""} onClick={() => void fetchModel()}>
                {busy === "model" ? "Working" : chosen?.downloaded ? "Use this" : "Download and use"}
              </Button>
              <Button variant="ghost" onClick={() => setStep(2)}>
                Later
              </Button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="mt-6 space-y-4">
            <dl className="grid grid-cols-[10rem_1fr] gap-y-2 text-xs">
              <dt className="text-text-secondary">Repositories found</dt>
              <dd>{state?.repositories ?? 0}</dd>
              <dt className="text-text-secondary">Sandbox</dt>
              <dd>
                {state?.sandbox ?? "unknown"}
                {state?.degraded ? " · weaker isolation, see System" : ""}
              </dd>
              <dt className="text-text-secondary">Model</dt>
              <dd>{state?.models_ready ? "running" : "starts with the first review"}</dd>
            </dl>
            <p className="text-xs text-text-secondary">
              Settings holds the rest: which repositories to skip, what to look for, your
              forges, and the tools an agent may call.
            </p>
            <Button onClick={() => void done()}>Start reviewing</Button>
          </div>
        )}

        <button
          className="mt-8 text-[11px] text-text-tertiary underline-offset-2 hover:underline"
          onClick={() => void done()}
        >
          Skip this. I will set it up myself.
        </button>
      </div>
    </div>
  );
}
