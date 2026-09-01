/** The system prompt.
 *
 * The prompt is the product: what counts as a defect, what to ignore, how hard to
 * judge. All of it is a sentence somebody wrote, and all of it is the user's to
 * rewrite. Start from one of the ready-made ones, or from nothing.
 *
 * One thing has to survive an edit. The parser reads the answer, so a prompt that
 * stops asking for the shape gives a review nothing can read. That is said plainly
 * before the save, and the save is still allowed: it is their prompt.
 */

import { Alert, AlertDescription, Button, Textarea } from "@metaphor-cloud/ui";
import { useCallback, useEffect, useState } from "react";

import { getPrompt } from "../engine";
import type { Prompt } from "../types";

export default function PromptEditor({
  rules,
  onSave,
}: {
  rules: string;
  onSave: (rules: string) => void;
}) {
  const [prompt, setPrompt] = useState<Prompt | null>(null);
  const [draft, setDraft] = useState("");
  const [ready, setReady] = useState(false);

  const load = useCallback(async (text: string | undefined) => {
    const body = await getPrompt(text);
    setPrompt(body);
    return body;
  }, []);

  // The saved prompt may be empty, meaning the one auger ships. The editor shows the
  // real words either way, because an empty box teaches nobody anything.
  useEffect(() => {
    void load(rules === "" ? undefined : rules).then((body) => {
      setDraft(body.rules);
      setReady(true);
    });
  }, [load, rules]);

  useEffect(() => {
    if (ready) void load(draft);
  }, [load, draft, ready]);

  const shipped = prompt?.shipped ?? "";
  const saved = draft.trim() === (rules.trim() || shipped.trim());
  const missing = prompt?.missing ?? [];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {(prompt?.presets ?? []).map((preset) => {
          const on = preset.system.trim() === draft.trim();
          return (
            <button
              key={preset.key}
              title={preset.summary}
              onClick={() => setDraft(preset.system)}
              className="whitespace-nowrap rounded-full border px-2.5 py-1 text-[11px] leading-none transition-colors"
              style={{
                borderColor: on ? "var(--color-accent)" : "var(--color-border-subtle)",
                color: on ? "var(--color-accent)" : "var(--color-text-tertiary)",
                background: on ? "var(--color-accent-glow)" : "transparent",
              }}
            >
              {preset.name}
            </button>
          );
        })}
      </div>
      <p className="text-xs text-text-secondary">
        {prompt?.presets.find((one) => one.system.trim() === draft.trim())?.summary ??
          "Your own prompt."}
      </p>

      <Textarea
        rows={18}
        className="font-mono text-[11px] leading-relaxed"
        value={draft}
        spellCheck={false}
        onChange={(event) => setDraft(event.target.value)}
      />

      {missing.length > 0 && (
        <Alert variant="warning">
          <AlertDescription>
            This prompt no longer asks for {missing.join(", ")}. Auger will not be able to
            read the reviewer's answers, and every run will record a bad answer.
          </AlertDescription>
        </Alert>
      )}

      <div className="flex items-center gap-2">
        <Button
          size="sm"
          disabled={saved}
          onClick={() => onSave(draft.trim() === shipped.trim() ? "" : draft)}
        >
          {saved ? "Saved" : "Save"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={draft.trim() === shipped.trim()}
          onClick={() => setDraft(shipped)}
        >
          Reset to default
        </Button>
        {!saved && <span className="text-[11px] text-warning">Not saved</span>}
      </div>
    </div>
  );
}
