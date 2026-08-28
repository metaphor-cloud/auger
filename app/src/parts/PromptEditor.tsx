/** What the reviewer is told.
 *
 * The rules and the output contract belong to the rig, because the parser depends on
 * the shape of the answer. What a review is *for* belongs to the user: one of the
 * ready-made sets, their own words, or both. The whole prompt is on show either way,
 * because nobody can judge a review without seeing what was asked for.
 */

import { Button, Textarea } from "@metaphor-cloud/ui";
import { useCallback, useEffect, useState } from "react";

import { getPrompt } from "../engine";
import type { Prompt } from "../types";

export default function PromptEditor({
  instructions,
  onSave,
}: {
  instructions: string;
  onSave: (instructions: string) => void;
}) {
  const [prompt, setPrompt] = useState<Prompt | null>(null);
  const [draft, setDraft] = useState(instructions);
  const [showing, setShowing] = useState(false);

  const load = useCallback(async (text: string) => {
    setPrompt(await getPrompt(text));
  }, []);

  useEffect(() => {
    setDraft(instructions);
  }, [instructions]);

  useEffect(() => {
    void load(draft);
  }, [load, draft]);

  const saved = draft.trim() === instructions.trim();

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {(prompt?.presets ?? []).map((preset) => {
          const on = preset.instructions.trim() === draft.trim();
          return (
            <button
              key={preset.key}
              title={preset.summary}
              onClick={() => setDraft(preset.instructions)}
              className="rounded-full border px-2.5 py-0.5 text-[11px] transition-colors"
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
        {prompt?.presets.find((one) => one.instructions.trim() === draft.trim())?.summary ??
          "Your own words."}
      </p>

      <Textarea
        rows={7}
        className="font-mono text-[11px]"
        value={draft}
        placeholder="Report security defects and data loss. Ignore performance."
        onChange={(event) => setDraft(event.target.value)}
      />

      <div className="flex items-center gap-2">
        <Button size="sm" disabled={saved} onClick={() => onSave(draft)}>
          {saved ? "Saved" : "Save"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setShowing((value) => !value)}>
          {showing ? "Hide the whole prompt" : "Show the whole prompt"}
        </Button>
        {!saved && <span className="text-[11px] text-warning">Not saved yet</span>}
      </div>

      {showing && prompt && (
        <div>
          <p className="mb-1 text-[11px] uppercase tracking-wider text-text-tertiary">
            What the model is sent. The rules and the answer format are the rig&apos;s, and
            they cannot be edited: the parser depends on them.
          </p>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded border border-border-subtle bg-bg p-3 font-mono text-[11px] leading-relaxed text-text-secondary">
            {prompt.system}
          </pre>
        </div>
      )}
    </div>
  );
}
